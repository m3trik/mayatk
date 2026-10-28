# !/usr/bin/python
# coding=utf-8
"""Test suite for the Audio Clips UI facade.

Covers the :class:`AudioClips` facade -- its composite and the
operations the Audio Clips panel calls -- under the single-scope,
per-track schema.  Primitive-level behavior (attr schema, key
read/write, compositor, batch orchestration) is covered by the
``test_audio_utils_*`` suites; the panel itself is tentacle's
``test/test_audio_clips_panel.py``.

Run inside Maya (standalone or GUI).
"""

import math
import os
import unittest
from unittest.mock import MagicMock, patch

try:
    import maya.cmds as cmds
except ImportError as exc:
    raise RuntimeError(
        "These tests must run inside a Maya session (standalone or GUI)."
    ) from exc

import pythontk as ptk

from base_test import MayaTkTestCase, make_temp_wav
from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils
from mayatk.audio_utils.audio_clips._audio_clips import AudioClips
from mayatk.core_utils.script_job_manager import ScriptJobManager


def _make_wav(name: str, duration_sec: float = 0.5, sr: int = 22050) -> str:
    return make_temp_wav(name, duration_sec, sr)


class TestCompositeCacheLocation(MayaTkTestCase):
    """The composite lands in the scene's project cache when its first clip
    lies outside every project (pre-release review 2026-10-13: it went to the
    per-user cache, which the saved scene then named)."""

    def test_a_clip_outside_any_project_mixes_into_the_scenes_project(self):
        import shutil
        import tempfile

        import pythontk as ptk

        project = os.path.realpath(tempfile.mkdtemp(prefix="composite_proj_"))
        outside = os.path.realpath(tempfile.mkdtemp(prefix="composite_outside_"))
        for folder in (project, outside):
            self.addCleanup(shutil.rmtree, folder, True)
        ptk.Workspace.create(project, create_dirs=False)
        source = os.path.join(outside, "clip.mp3")
        if ptk.Workspace.find_containing(source) is not None:
            self.skipTest("the temp dir lies inside a marked project here")
        os.makedirs(os.path.join(project, "scenes"))
        cmds.file(rename=os.path.join(project, "scenes", "shot.ma"))
        cmds.file(save=True, type="mayaAscii", force=True)

        with (
            patch.object(audio_utils, "load_file_map", return_value={"clip": source}),
            patch.object(audio_utils, "list_tracks", return_value=[]),
        ):
            _, _, cache_dir = AudioClips._gather_composite_inputs()

        self.assertEqual(
            os.path.normcase(os.path.normpath(cache_dir)),
            os.path.normcase(os.path.join(project, "cache", "audio")),
        )


class TestLoadTracks(MayaTkTestCase):
    def test_registers_tracks_and_paths(self):
        wav_a = _make_wav("load_a")
        wav_b = _make_wav("load_b")

        tids = AudioClips.load_tracks([wav_a, wav_b])

        self.assertEqual(sorted(tids), ["load_a", "load_b"])
        self.assertIn("load_a", audio_utils.list_tracks())
        self.assertIn("load_b", audio_utils.list_tracks())
        self.assertEqual(audio_utils.get_path("load_a"), wav_a)
        self.assertEqual(audio_utils.get_path("load_b"), wav_b)

    def test_readd_same_stem_replaces_path(self):
        import shutil

        wav_v1 = _make_wav("repl_track")
        AudioClips.load_tracks([wav_v1])
        audio_utils.write_key("repl_track", frame=10, value=1)

        # A DIFFERENT directory carrying the SAME stem -- beside the fixture
        # dir (system temp), so no wav write touches the synced drive.
        other_dir = os.path.join(os.path.dirname(wav_v1), "other")
        os.makedirs(other_dir, exist_ok=True)
        wav_v2 = os.path.join(other_dir, "repl_track.wav").replace("\\", "/")
        shutil.copy2(wav_v1, wav_v2)

        tids = AudioClips.load_tracks([wav_v2])
        self.assertEqual(tids, ["repl_track"])
        self.assertEqual(audio_utils.get_path("repl_track"), wav_v2)

        keys = audio_utils.read_keys("repl_track")
        self.assertEqual(keys, [(10.0, 1.0)])

    def test_empty_list_returns_empty(self):
        self.assertEqual(AudioClips.load_tracks([]), [])


class TestSync(MayaTkTestCase):
    def test_sync_creates_dg_node_for_each_track(self):
        wav = _make_wav("sync_one")
        AudioClips.load_tracks([wav])
        audio_utils.write_key("sync_one", frame=0, value=1)

        AudioClips.sync()
        dg = audio_utils.find_dg_node_for_track("sync_one")
        self.assertIsNotNone(dg)
        self.assertTrue(cmds.objExists(str(dg)))

    def test_sync_builds_composite_when_tracks_keyed(self):
        wav = _make_wav("comp_one", duration_sec=0.3)
        AudioClips.load_tracks([wav])
        audio_utils.write_key("comp_one", frame=0, value=1)

        AudioClips.sync()
        comp = AudioClips._find_composite_node()
        self.assertIsNotNone(comp)
        path = cmds.getAttr(f"{comp}.filename")
        self.assertTrue(path and os.path.isfile(path))

    def test_composite_caches_in_the_shared_audio_cache_named_for_the_scene(self):
        """The composite the scene's sound node plays sits in the one audio
        cache (``AudioUtils._cache_dirs_for``: the source's project
        ``cache/audio``, else the open scene's, else the per-user cache) --
        never a
        ``_maya_audio_cache`` folder beside the first source -- under a name
        of its own scene, so two scenes sharing a cache never play each
        other's mix. Added: 2026-10-13"""
        wav = _make_wav("comp_loc", duration_sec=0.3)
        AudioClips.load_tracks([wav])
        audio_utils.write_key("comp_loc", frame=0, value=1)
        AudioClips.sync()
        path = cmds.getAttr(f"{AudioClips._find_composite_node()}.filename")
        self.assertEqual(
            os.path.normcase(os.path.normpath(os.path.dirname(path))),
            os.path.normcase(os.path.normpath(audio_utils._cache_dirs_for(wav)[0])),
        )
        self.assertFalse(
            os.path.isdir(os.path.join(os.path.dirname(wav), "_maya_audio_cache")),
            "a cache folder was written beside the source",
        )
        self.assertTrue(os.path.basename(path).startswith("untitled_"), path)
        self.assertTrue(path.endswith(AudioClips.COMPOSITE_FILENAME), path)

        # Removing the composite still deletes its scene-named file.
        AudioClips._remove_composite_node()
        self.assertFalse(os.path.exists(path))

    def test_sync_no_keys_no_composite(self):
        wav = _make_wav("nokey")
        AudioClips.load_tracks([wav])
        AudioClips.sync()
        self.assertIsNone(AudioClips._find_composite_node())

    def test_composite_is_marked_and_ignored_by_compositor(self):
        wav = _make_wav("mark_one", duration_sec=0.3)
        AudioClips.load_tracks([wav])
        audio_utils.write_key("mark_one", frame=0, value=1)
        AudioClips.sync()

        comp = AudioClips._find_composite_node()
        self.assertIsNotNone(comp)
        self.assertTrue(
            cmds.attributeQuery(
                AudioClips.COMPOSITE_MARKER_ATTR, node=comp, exists=True
            )
        )
        self.assertFalse(audio_utils.is_managed_dg(comp))


class TestRebuildComposite(MayaTkTestCase):
    def test_no_tracks_returns_none(self):
        self.assertIsNone(AudioClips.rebuild_composite())

    def test_tracks_without_keys_returns_none(self):
        wav = _make_wav("reb_nokey")
        AudioClips.load_tracks([wav])
        self.assertIsNone(AudioClips.rebuild_composite())

    def test_creates_marked_node_with_file(self):
        wav = _make_wav("reb_go", duration_sec=0.3)
        AudioClips.load_tracks([wav])
        audio_utils.write_key("reb_go", frame=5, value=1)

        node = AudioClips.rebuild_composite()
        self.assertIsNotNone(node)
        self.assertEqual(
            cmds.getAttr(f"{node}.{AudioClips.COMPOSITE_MARKER_ATTR}"),
            AudioClips.COMPOSITE_MARKER_VALUE,
        )
        self.assertTrue(os.path.isfile(cmds.getAttr(f"{node}.filename")))


class TestRemove(MayaTkTestCase):
    def test_remove_deletes_tracks_dg_and_composite(self):
        wav = _make_wav("rm_all", duration_sec=0.3)
        AudioClips.load_tracks([wav])
        audio_utils.write_key("rm_all", frame=0, value=1)
        AudioClips.sync()

        self.assertIn("rm_all", audio_utils.list_tracks())
        self.assertIsNotNone(audio_utils.find_dg_node_for_track("rm_all"))
        self.assertIsNotNone(AudioClips._find_composite_node())

        AudioClips.remove()
        self.assertEqual(audio_utils.list_tracks(), [])
        self.assertIsNone(audio_utils.find_dg_node_for_track("rm_all"))
        self.assertIsNone(AudioClips._find_composite_node())

    def test_remove_when_nothing_present_returns_zero_or_none(self):
        # should not raise
        AudioClips.remove()
        self.assertEqual(audio_utils.list_tracks(), [])

    def test_remove_clears_file_map(self):
        wav = _make_wav("rm_map")
        AudioClips.load_tracks([wav])
        self.assertTrue(audio_utils.load_file_map())

        AudioClips.remove()
        self.assertEqual(audio_utils.load_file_map(), {})


class TestListNodes(MayaTkTestCase):
    def test_lists_dg_and_composite(self):
        wav = _make_wav("list_one", duration_sec=0.3)
        AudioClips.load_tracks([wav])
        audio_utils.write_key("list_one", frame=0, value=1)
        AudioClips.sync()

        nodes = AudioClips.list_nodes()
        dg = audio_utils.find_dg_node_for_track("list_one")
        comp = AudioClips._find_composite_node()
        self.assertIn(dg, nodes)
        self.assertIn(comp, nodes)

    def test_empty_scene_returns_empty(self):
        self.assertEqual(AudioClips.list_nodes(), [])


class TestPanelOperations(MayaTkTestCase):
    """The operations tentacle's shared Audio Clips panel calls on
    ``mtk.AudioClips`` (blendertk's ``AudioClips`` mirrors the shared ones)."""

    def test_the_panel_vocabulary(self):
        self.assertEqual(
            (AudioClips.ITEM_NOUN, AudioClips.PLACE_LABEL, AudioClips.END_KEYS),
            ("Track", "Key Audio Event", True),
        )
        # The Time Slider plays WAV/AIFF; the rest convert (Auto Convert).
        self.assertLess(
            AudioClips.PLAYABLE_EXTENSIONS, ptk.AudioUtils.SOURCE_EXTENSIONS
        )

    def test_add_tracks_registers_and_resyncs_once_a_track_is_keyed(self):
        wav = _make_wav("add_sync", duration_sec=0.3)
        self.assertEqual(AudioClips.add_tracks([wav]), ["add_sync"])
        self.assertIn("add_sync", audio_utils.list_tracks())
        audio_utils.write_key("add_sync", frame=0, value=1)
        AudioClips.add_tracks([wav])
        self.assertIsNotNone(audio_utils.find_dg_node_for_track("add_sync"))

    def test_find_track_answers_to_the_normalized_name(self):
        AudioClips.load_tracks([_make_wav("find_me")])
        self.assertEqual(AudioClips.list_tracks(), ["find_me"])
        self.assertEqual(AudioClips.find_track("Find Me"), "find_me")
        self.assertIsNone(AudioClips.find_track("nowhere"))
        self.assertIsNone(AudioClips.find_track("!!!"))

    def test_key_track_keys_on_at_the_playhead(self):
        AudioClips.load_tracks([_make_wav("key_start")])
        cmds.currentTime(10)
        self.assertEqual(AudioClips.key_track("key_start"), 10.0)
        self.assertEqual(audio_utils.read_keys("key_start"), [(10.0, 1.0)])

    def test_key_track_auto_end_keys_off_where_the_file_ends(self):
        AudioClips.load_tracks([_make_wav("key_end", duration_sec=0.5)])
        AudioClips.key_track("key_end", frame=0, auto_end=True)
        keys = audio_utils.read_keys("key_end")
        self.assertEqual(keys[0], (0.0, 1.0))
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[1][1], 0.0)
        self.assertGreater(keys[1][0], 0.0)
        self.assertIsNotNone(AudioClips._find_composite_node(), "keying syncs")

    def test_key_track_auto_end_skips_an_off_inside_a_later_start(self):
        AudioClips.load_tracks([_make_wav("key_skip", duration_sec=2.0)])
        audio_utils.write_key("key_skip", frame=30, value=1)
        AudioClips.key_track("key_skip", frame=0, auto_end=True)
        values = [int(round(v)) for _, v in audio_utils.read_keys("key_skip")]
        self.assertEqual((values.count(1), values.count(0)), (2, 0))

    def test_key_all_lays_every_track_end_to_end(self):
        AudioClips.load_tracks(
            [_make_wav("ka_a", duration_sec=0.5), _make_wav("ka_b", duration_sec=0.5)]
        )
        self.assertEqual(AudioClips.key_all(frame=10, stagger=2), ["ka_a", "ka_b"])
        self.assertEqual(audio_utils.read_keys("ka_a"), [(10.0, 1.0)])
        second = math.ceil(10 + audio_utils.clip_length_frames("ka_a")) + 2
        self.assertEqual(audio_utils.read_keys("ka_b"), [(float(second), 1.0)])

    def test_next_track_follows_the_latest_start(self):
        AudioClips.load_tracks(
            [_make_wav("nt_a"), _make_wav("nt_b"), _make_wav("nt_c")]
        )
        self.assertEqual(AudioClips.next_track(), "nt_a", "nothing keyed: the first")
        audio_utils.write_key("nt_a", frame=0, value=1)
        audio_utils.write_key("nt_b", frame=20, value=1)
        self.assertEqual(AudioClips.next_track(), "nt_c")
        audio_utils.write_key("nt_c", frame=30, value=1)
        self.assertEqual(AudioClips.next_track(), "nt_a", "wraps")

    def test_next_track_without_tracks(self):
        self.assertIsNone(AudioClips.next_track())

    def test_tracks_at_names_the_tracks_playing_there(self):
        AudioClips.load_tracks([_make_wav("at_a"), _make_wav("at_b")])
        audio_utils.write_key("at_a", frame=0, value=1)
        audio_utils.write_key("at_a", frame=10, value=0)
        audio_utils.write_key("at_b", frame=5, value=1)
        self.assertEqual(AudioClips.tracks_at(7), ["at_a", "at_b"])
        self.assertEqual(AudioClips.tracks_at(12), ["at_b"])

    def test_cleanup_unused_deletes_the_unkeyed_tracks(self):
        AudioClips.load_tracks([_make_wav("keep_keyed"), _make_wav("drop_unkeyed")])
        audio_utils.write_key("keep_keyed", frame=0, value=1)
        self.assertEqual(AudioClips.cleanup_unused(), ["drop_unkeyed"])
        self.assertEqual(audio_utils.list_tracks(), ["keep_keyed"])
        self.assertEqual(AudioClips.cleanup_unused(), [])

    def test_remove_track_deletes_one_track_and_its_file(self):
        AudioClips.load_tracks([_make_wav("rm_one"), _make_wav("rm_keep")])
        self.assertTrue(AudioClips.remove_track("rm_one"))
        self.assertEqual(audio_utils.list_tracks(), ["rm_keep"])
        self.assertNotIn("rm_one", audio_utils.load_file_map())
        self.assertFalse(AudioClips.remove_track("rm_one"))

    def test_rename_track_normalizes_and_keeps_the_keys(self):
        AudioClips.load_tracks([_make_wav("ren_a"), _make_wav("ren_b")])
        audio_utils.write_key("ren_a", frame=4, value=1)
        self.assertEqual(
            AudioClips.rename_track("ren_a", "Footstep Left"), "footstep_left"
        )
        self.assertEqual(audio_utils.read_keys("footstep_left"), [(4.0, 1.0)])
        with self.assertRaisesRegex(ValueError, "name is taken"):
            AudioClips.rename_track("footstep_left", "ren_b")
        with self.assertRaisesRegex(ValueError, "Invalid track name"):
            AudioClips.rename_track("footstep_left", "!!!")

    def test_replace_track_takes_the_new_files_name(self):
        AudioClips.load_tracks([_make_wav("rep_old")])
        audio_utils.write_key("rep_old", frame=3, value=1)
        new = _make_wav("rep_new")
        self.assertEqual(AudioClips.replace_track("rep_old", new), "rep_new")
        self.assertEqual(audio_utils.get_path("rep_new"), new)
        self.assertEqual(audio_utils.read_keys("rep_new"), [(3.0, 1.0)])
        # The same stem swaps the file and keeps the id.
        self.assertEqual(AudioClips.replace_track("rep_new", new), "rep_new")

    def test_reveal_and_channels_config_name_the_carrier(self):
        AudioClips.load_tracks([_make_wav("rev_one")])
        carrier = audio_utils.CARRIER_NODE
        self.assertEqual(AudioClips.reveal("rev_one"), carrier)
        self.assertEqual(cmds.ls(selection=True), [carrier])
        self.assertEqual(
            AudioClips.channels_config(),
            {
                "targets": [carrier],
                "filter": "Custom",
                "search": f"{audio_utils.ATTR_PREFIX}*",
            },
        )

    def test_reveal_and_channels_config_refuse_before_any_track(self):
        if cmds.objExists(audio_utils.CARRIER_NODE):
            self.skipTest("this build makes the carrier with every scene")
        with self.assertRaisesRegex(ValueError, "does not exist yet"):
            AudioClips.reveal()
        with self.assertRaisesRegex(ValueError, "does not exist yet"):
            AudioClips.channels_config()

    def test_activate_track_puts_its_sound_node_on_the_time_slider(self):
        AudioClips.load_tracks([_make_wav("act_one", duration_sec=0.3)])
        audio_utils.write_key("act_one", frame=0, value=1)
        AudioClips.sync()
        node = audio_utils.find_dg_node_for_track("act_one")
        self.assertIsNotNone(node)
        self.assertEqual(AudioClips.activate_track("act_one"), node)
        self.assertIsNone(AudioClips.activate_track("no_such_track"))

    def test_attach_composite_reattaches_an_existing_mix(self):
        AudioClips.load_tracks([_make_wav("att_one", duration_sec=0.3)])
        audio_utils.write_key("att_one", frame=0, value=1)
        AudioClips.sync()
        self.assertIsNotNone(AudioClips.attach_composite())
        self.assertEqual(
            AudioClips.attach_composite(), AudioClips._find_composite_node()
        )

    def test_has_legacy_data_is_false_on_a_clean_scene(self):
        self.assertFalse(AudioClips.has_legacy_data())

    def test_export_composite_writes_the_mix_where_the_dialog_says(self):
        AudioClips.load_tracks([_make_wav("exp_comp", duration_sec=0.3)])
        audio_utils.write_key("exp_comp", frame=0, value=1)
        offered = []
        with ptk.TempArtifacts(prefix="mtk_audio_export_") as tmp:
            dest = tmp.path(".wav")
            result = AudioClips.export_composite(
                lambda start: offered.append(start) or dest, trim_silence=False
            )
            self.assertEqual(result, (dest, False))
            self.assertTrue(os.path.isfile(dest))
        self.assertTrue(offered[0].endswith("audio_composite.wav"), offered)
        self.assertEqual(AudioClips.export_composite(lambda start: None), (None, False))

    def test_export_composite_refuses_with_nothing_keyed(self):
        AudioClips.load_tracks([_make_wav("exp_none")])
        with self.assertRaisesRegex(ValueError, "No composite WAV"):
            AudioClips.export_composite(lambda start: self.fail("no dialog"))

    def test_export_tracks_copies_each_keyed_file_with_its_range(self):
        AudioClips.load_tracks(
            [_make_wav("exp_keyed", duration_sec=0.5), _make_wav("exp_unkeyed")]
        )
        audio_utils.write_key("exp_keyed", frame=12, value=1)
        audio_utils.write_key("exp_keyed", frame=40, value=0)
        with ptk.TempArtifacts(prefix="mtk_audio_export_") as tmp:
            folder = tmp.dir_path(name="keyed")
            self.assertEqual(
                AudioClips.export_tracks(
                    lambda start: folder, keyed_only=True, suffix_range=True
                ),
                (1, [], folder),
            )
            self.assertEqual(os.listdir(folder), ["exp_keyed_12-40.wav"])
            every = tmp.dir_path(name="every")
            self.assertEqual(
                AudioClips.export_tracks(lambda start: every, keyed_only=False),
                (2, [], every),
            )
        self.assertEqual(
            AudioClips.export_tracks(lambda start: None, keyed_only=False),
            (0, [], None),
        )
        audio_utils.clear_keys("exp_keyed")
        with self.assertRaisesRegex(ValueError, "No keyed tracks"):
            AudioClips.export_tracks(lambda start: self.fail("no dialog"))

    def test_watch_subscribes_scene_and_time_for_the_owner(self):
        owner, widget = object(), object()
        jobs = MagicMock()
        jobs.subscribe.side_effect = [1, 2, 3]
        times = []
        with patch.object(ScriptJobManager, "instance", return_value=jobs):
            tokens = AudioClips.watch(
                owner, on_scene=lambda: None, on_time=times.append, widget=widget
            )
        self.assertEqual(tokens, [1, 2, 3])
        self.assertEqual(
            [c.args[0] for c in jobs.subscribe.call_args_list],
            ["SceneOpened", "NewSceneOpened", "timeChanged"],
        )
        self.assertTrue(
            all(c.kwargs["owner"] is owner for c in jobs.subscribe.call_args_list)
        )
        jobs.connect_cleanup.assert_called_once_with(widget, owner=owner)
        cmds.currentTime(7)
        jobs.subscribe.call_args_list[2].args[1]()
        self.assertEqual(times, [7.0])

    def test_the_carrier_watch_reports_a_track_attribute_added(self):
        AudioClips.load_tracks([_make_wav("cw_one")])
        calls, owner = [], object()
        self.assertIsNotNone(AudioClips._watch_carrier(lambda: calls.append(1), owner))
        self.addCleanup(ScriptJobManager.instance().unsubscribe_all, owner)
        audio_utils.ensure_track_attr("cw_two")
        self.assertTrue(calls)


class TestCompositeCollision(MayaTkTestCase):
    def test_find_composite_adopts_unstamped_canonical_name(self):
        ext = cmds.createNode("audio", name=AudioClips.COMPOSITE_NODE, skipSelect=True)
        self.assertEqual(ext, AudioClips.COMPOSITE_NODE)

        found = AudioClips._find_composite_node()
        self.assertEqual(found, AudioClips.COMPOSITE_NODE)
        self.assertEqual(
            cmds.getAttr(f"{found}.{AudioClips.COMPOSITE_MARKER_ATTR}"),
            AudioClips.COMPOSITE_MARKER_VALUE,
        )

    def test_compositor_sync_does_not_touch_composite(self):
        wav = _make_wav("no_touch", duration_sec=0.3)
        AudioClips.load_tracks([wav])
        audio_utils.write_key("no_touch", frame=0, value=1)
        AudioClips.sync()

        comp = AudioClips._find_composite_node()
        self.assertIsNotNone(comp)

        audio_utils.sync()
        self.assertTrue(cmds.objExists(str(comp)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
