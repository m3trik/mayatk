# !/usr/bin/python
# coding=utf-8
"""Verify AudioClips.prepare_for_export() stamps the manifest and FBX carries it through."""

import os
import json
import unittest

import maya.cmds as cmds
import maya.mel as mel
import pythontk as ptk

from base_test import MayaTkTestCase
from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils
from mayatk.audio_utils.audio_clips._audio_clips import AudioClips


def _event_set(manifest: str):
    """Parse a v2 manifest into a comparable ``{(clip, frame, name)}`` set."""
    payload = json.loads(manifest)
    return {(e["clip"], e["frame"], e["name"]) for e in payload["events"]}


class TestAudioClipsExport(MayaTkTestCase):
    """Verify AudioClips exports the scene-wide audio manifest for Unity."""

    def setUp(self):
        super().setUp()
        self.fbx_path = self.temp_path("debug_audio_clips.fbx")
        if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
            try:
                cmds.loadPlugin("fbxmaya")
            except Exception:
                self.skipTest("fbxmaya plugin not available")

    def _seed_tracks(self):
        """Create two keyed tracks on the canonical carrier."""
        # Pin the playback range so the manifest offset is deterministic.
        # prepare_for_export subtracts playbackOptions.min from each frame
        # so the wire format aligns with how Unity reads the imported FBX
        # (Maya frame ``min`` becomes Unity time 0).
        cmds.playbackOptions(min=1, max=60)
        audio_utils.write_key("footstep", frame=10, value=1)
        audio_utils.write_key("footstep", frame=15, value=0)
        audio_utils.write_key("jump", frame=24, value=1)
        audio_utils.write_key("jump", frame=28, value=0)

    def test_prepare_for_export_stamps_manifest(self):
        """prepare_for_export writes ``audio`` as a plain export channel."""
        self._seed_tracks()
        manifest = AudioClips.prepare_for_export()

        from mayatk.node_utils.data_nodes import DataNodes

        stored = ptk.SceneRecords.AUDIO.read_text(DataNodes) or ""
        self.assertEqual(manifest, stored)
        # A regenerated artifact lives ONLY on data_export — no internal copy.
        self.assertFalse(
            cmds.attributeQuery(
                AudioClips.MANIFEST_ATTR, node=DataNodes.INTERNAL, exists=True
            ),
            "manifest must not be authored on data_internal (derived artifact)",
        )
        # v2 envelope: versioned JSON, unscoped events (no takes published),
        # Maya frames 10/24 shifted by playback_min=1.
        payload = json.loads(stored)
        self.assertEqual(payload["version"], AudioClips.MANIFEST_VERSION)
        # The frames' rate travels with them (2026-10-17: frames alone left a
        # reader to guess it).
        self.assertEqual(payload["fps"], audio_utils.get_fps())
        self.assertEqual(
            _event_set(stored),
            {("", 9, "footstep"), ("", 23, "jump")},
        )

    @staticmethod
    def _publish_clips(*clips):
        """Publish the shot record as ``ShotStore.publish_export_view`` writes
        it: each clip carrying its own range (``(name, start, end)``)."""
        from mayatk.node_utils.data_nodes import DataNodes

        ptk.SceneRecords.SHOTS.save(
            DataNodes,
            {
                "shots": [
                    {"clip": name, "start": start, "end": end, "objects": []}
                    for name, start, end in clips
                ]
            },
        )

    def test_prepare_for_export_scopes_events_to_takes(self):
        """With the shots published, events land in their clip, frames rebased
        to the clip start — the same origin the imported AnimationClip counts
        from. Events outside every clip are dropped."""
        self._seed_tracks()  # footstep on @10, jump on @24
        audio_utils.write_key("stray", frame=59, value=1)  # outside both takes
        self._publish_clips(("Intro", 1, 20), ("Outro", 21, 50))

        manifest = AudioClips.prepare_for_export()
        self.assertEqual(
            _event_set(manifest),
            {("Intro", 9, "footstep"), ("Outro", 3, "jump")},
        )

    def test_scoping_skips_takes_missing_range(self):
        """A published clip without start/end must scope nothing — the old
        ``get(..., 0)`` defaults turned it into a phantom ``(0, 0)`` take
        that swallowed frame-0 events (a clip without its range is not a
        take at all)."""
        from mayatk.node_utils.data_nodes import DataNodes

        self._seed_tracks()  # footstep @10, jump @24
        audio_utils.write_key(
            "boom", frame=0, value=1
        )  # only the phantom (0,0) contains it
        ptk.SceneRecords.SHOTS.save(
            DataNodes,
            {
                "shots": [
                    {"clip": "Broken", "objects": []},  # no range — ignored
                    {"clip": "Intro", "start": 1, "end": 30, "objects": []},
                ]
            },
        )

        manifest = AudioClips.prepare_for_export()
        self.assertEqual(
            _event_set(manifest),
            {("Intro", 9, "footstep"), ("Intro", 23, "jump")},
            "a range-less take must not capture events (boom@0 is outside "
            "every real take and should be dropped)",
        )

    def test_scoping_falls_back_unscoped_when_no_take_has_a_range(self):
        """When no published clip carries a usable range, a shot record
        declares no take: bake unscoped rather than silently dropping every
        event."""
        from mayatk.node_utils.data_nodes import DataNodes

        self._seed_tracks()
        ptk.SceneRecords.SHOTS.save(
            DataNodes, {"shots": [{"clip": "Broken", "objects": []}]}
        )

        manifest = AudioClips.prepare_for_export()
        self.assertTrue(manifest, "manifest should fall back to an unscoped bake")
        self.assertEqual(
            _event_set(manifest),
            {("", 9, "footstep"), ("", 23, "jump")},
        )

    def test_a_full_sequence_export_ships_its_events_unscoped(self):
        """Full Sequence Only splits nothing: the FBX carries ONE whole-timeline
        take, so events scoped to the shots named clips the file does not have
        and Unity's AudioEventController injected none of them. The shots
        still ride as metadata; the events follow the take that ships.
        Added: 2026-09-18
        """
        self._seed_tracks()  # footstep on @10, jump on @24; playback min 1
        self._publish_clips(("Intro", 1, 20), ("Outro", 21, 50))
        full = AudioClips.export_record(ptk.ExportContext(clip_mode="full"))
        self.assertEqual(
            _event_set(full.text), {("", 9, "footstep"), ("", 23, "jump")}
        )
        for mode in ("shots", "both", None):
            with self.subTest(clip_mode=mode):
                split = AudioClips.export_record(ptk.ExportContext(clip_mode=mode))
                self.assertEqual(
                    _event_set(split.text),
                    {("Intro", 9, "footstep"), ("Outro", 3, "jump")},
                )

    def test_an_unscoped_event_counts_from_the_first_frame_the_file_holds(self):
        """An unscoped event's frame counts from the first frame the file's
        animation holds: the clip origin the pipeline measured
        (``ctx.clip_span``), else the range the write bakes, else the playback
        start -- blendertk's chain, so one scene ships the same frames from
        either DCC. Counted from the playback start alone, an export whose
        playback range was not its exported range shipped every event off by
        the difference. Added: 2026-10-20
        """
        self._seed_tracks()  # footstep on @10, jump on @24; playback min 1
        measured = AudioClips.export_record(ptk.ExportContext(clip_span=(5.0, 60.0)))
        self.assertEqual(
            _event_set(measured.text), {("", 5, "footstep"), ("", 19, "jump")}
        )
        self.addCleanup(mel.eval, "FBXResetExport")
        mel.eval("FBXResetExport")
        mel.eval("FBXExportBakeComplexAnimation -v true")
        mel.eval("FBXExportBakeComplexStart -v 8")
        mel.eval("FBXExportBakeComplexEnd -v 60")
        baked = AudioClips.export_record(ptk.ExportContext())
        self.assertEqual(
            _event_set(baked.text), {("", 2, "footstep"), ("", 16, "jump")}
        )
        mel.eval("FBXExportBakeComplexAnimation -v false")
        played = AudioClips.export_record(ptk.ExportContext())
        self.assertEqual(
            _event_set(played.text), {("", 9, "footstep"), ("", 23, "jump")}
        )

    def test_an_unscoped_event_before_the_first_frame_is_dropped_and_said_where(self):
        """One before the first frame the file's animation holds can never
        fire: dropped, and the warning says where it fell -- not "outside
        every shot take" when no take ships. Added: 2026-10-20
        """
        self._seed_tracks()  # footstep on @10, jump on @24
        with self.assertLogs(AudioClips.logger, level="WARNING") as logs:
            record = AudioClips.export_record(
                ptk.ExportContext(clip_span=(12.0, 60.0))
            )
        self.assertEqual(_event_set(record.text), {("", 12, "jump")})
        self.assertTrue(
            any(
                "footstep@10" in line and "before" in line and "shot take" not in line
                for line in logs.output
            ),
            logs.output,
        )

    def test_prepare_for_export_migrates_legacy_proxy(self):
        """A pre-taxonomy proxied manifest pair is replaced by the plain channel."""
        from mayatk.node_utils.data_nodes import DataNodes

        # Recreate the old shape (what the retired ``mirror_attr`` mechanism
        # built): authored on internal, Maya-proxied on export.
        internal = str(DataNodes.ensure_internal())
        export = str(DataNodes.ensure_export())
        cmds.addAttr(internal, longName=AudioClips.MANIFEST_ATTR, dataType="string")
        cmds.addAttr(
            export,
            longName=AudioClips.MANIFEST_ATTR,
            proxy=f"{internal}.{AudioClips.MANIFEST_ATTR}",
        )
        cmds.setAttr(
            f"{DataNodes.INTERNAL}.{AudioClips.MANIFEST_ATTR}",
            "1:legacy",
            type="string",
        )

        self._seed_tracks()
        manifest = AudioClips.prepare_for_export()

        self.assertIn(("", 9, "footstep"), _event_set(manifest))
        self.assertEqual(ptk.SceneRecords.AUDIO.read_text(DataNodes), manifest)
        self.assertFalse(
            cmds.addAttr(
                f"{DataNodes.EXPORT}.{AudioClips.MANIFEST_ATTR}",
                query=True,
                usedAsProxy=True,
            ),
            "export channel should be a plain attr after migration, not a proxy",
        )
        self.assertFalse(
            cmds.attributeQuery(
                AudioClips.MANIFEST_ATTR, node=DataNodes.INTERNAL, exists=True
            ),
            "legacy internal source attr should be dropped",
        )

    def test_prepare_for_export_is_idempotent(self):
        """Repeated calls overwrite cleanly; no duplicate attrs."""
        self._seed_tracks()
        first = AudioClips.prepare_for_export()
        second = AudioClips.prepare_for_export()
        self.assertEqual(first, second)

    def test_prepare_for_export_empty_scene(self):
        """Returns empty string when there are no keyed tracks."""
        manifest = AudioClips.prepare_for_export()
        self.assertEqual(manifest, "")

    def test_manifest_survives_fbx_export(self):
        """The baked manifest reaches the FBX as a user property on data_export."""
        self._seed_tracks()
        AudioClips.prepare_for_export()

        from mayatk.node_utils.data_nodes import DataNodes

        self.assertTrue(
            cmds.objExists(DataNodes.EXPORT), "data_export transform should exist"
        )
        # Export the whole scene so the data_export transform is included.
        mel.eval("FBXResetExport")
        mel.eval("FBXExportInAscii -v true")
        mel.eval(f'FBXExport -f "{self.fbx_path.replace(os.sep, "/")}"')
        self.assertTrue(os.path.exists(self.fbx_path), "FBX was not created")

        content = self.read_text_settled(
            self.fbx_path, encoding="utf-8", errors="ignore"
        )

        self.assertIn(
            "data_export",
            content,
            "data_export transform should be present in the exported FBX",
        )
        self.assertIn(
            '"audio"',
            content,
            "the audio record's property should be present in the exported FBX",
        )
        # Quote-free assertion: FBX ASCII escapes embedded quotes in string
        # values, so match the bare label rather than JSON punctuation.
        self.assertIn(
            "footstep",
            content,
            "manifest event name should appear inside the FBX user-property block",
        )


if __name__ == "__main__":
    unittest.main()
