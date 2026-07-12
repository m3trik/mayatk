# !/usr/bin/python
# coding=utf-8
"""Verify AudioClips.prepare_for_export() stamps the manifest and FBX carries it through."""
import os
import json
import unittest

import maya.cmds as cmds
import maya.mel as mel

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
        self.fbx_path = os.path.join(
            r"O:/Cloud/Code/_scripts/mayatk/test", "debug_audio_clips.fbx"
        )
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
        """prepare_for_export writes ``audio_manifest`` as a plain export channel."""
        self._seed_tracks()
        manifest = AudioClips.prepare_for_export()

        from mayatk.node_utils.data_nodes import DataNodes

        stored = DataNodes.get_export_string(AudioClips.MANIFEST_ATTR) or ""
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
        self.assertEqual(
            _event_set(stored),
            {("", 9, "footstep"), ("", 23, "jump")},
        )

    def test_prepare_for_export_scopes_events_to_takes(self):
        """With fbx_takes published, events land in their take, frames rebased
        to the take start — the same origin the imported AnimationClip counts
        from. Events outside every take are dropped."""
        from mayatk.node_utils.data_nodes import DataNodes

        self._seed_tracks()  # footstep on @10, jump on @24
        audio_utils.write_key("stray", frame=59, value=1)  # outside both takes
        DataNodes.set_export_string(
            DataNodes.FBX_TAKES,
            json.dumps(
                [
                    {"name": "Intro", "start": 1, "end": 20},
                    {"name": "Outro", "start": 21, "end": 50},
                ]
            ),
        )

        manifest = AudioClips.prepare_for_export()
        self.assertEqual(
            _event_set(manifest),
            {("Intro", 9, "footstep"), ("Outro", 3, "jump")},
        )

    def test_scoping_skips_takes_missing_range(self):
        """A published take without start/end must scope nothing — the old
        ``get(..., 0)`` defaults turned it into a phantom ``(0, 0)`` take
        that swallowed frame-0 events."""
        from mayatk.node_utils.data_nodes import DataNodes

        self._seed_tracks()  # footstep @10, jump @24
        audio_utils.write_key("boom", frame=0, value=1)  # only the phantom (0,0) contains it
        DataNodes.set_export_string(
            DataNodes.FBX_TAKES,
            json.dumps(
                [
                    {"name": "Broken"},  # no range — must be ignored
                    {"name": "Intro", "start": 1, "end": 30},
                ]
            ),
        )

        manifest = AudioClips.prepare_for_export()
        self.assertEqual(
            _event_set(manifest),
            {("Intro", 9, "footstep"), ("Intro", 23, "jump")},
            "a range-less take must not capture events (boom@0 is outside "
            "every real take and should be dropped)",
        )

    def test_scoping_falls_back_unscoped_when_no_take_has_a_range(self):
        """When the published takes carry no usable ranges at all, treat the
        channel like any other malformed ``fbx_takes`` payload: bake unscoped
        rather than silently dropping every event."""
        from mayatk.node_utils.data_nodes import DataNodes

        self._seed_tracks()
        DataNodes.set_export_string(
            DataNodes.FBX_TAKES, json.dumps([{"name": "Broken"}])
        )

        manifest = AudioClips.prepare_for_export()
        self.assertTrue(manifest, "manifest should fall back to an unscoped bake")
        self.assertEqual(
            _event_set(manifest),
            {("", 9, "footstep"), ("", 23, "jump")},
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
        self.assertEqual(DataNodes.get_export_string(AudioClips.MANIFEST_ATTR), manifest)
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

        with open(self.fbx_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        self.assertIn(
            "data_export",
            content,
            "data_export transform should be present in the exported FBX",
        )
        self.assertIn(
            '"audio_manifest"',
            content,
            "audio_manifest property should be present in the exported FBX",
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
