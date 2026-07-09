# !/usr/bin/python
# coding=utf-8
"""Verify AudioClips.prepare_for_export() stamps the manifest and FBX carries it through."""
import os
import unittest

import maya.cmds as cmds
import maya.mel as mel

from base_test import MayaTkTestCase
from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils
from mayatk.audio_utils.audio_clips._audio_clips import AudioClips


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
        # Maya frames 10/24 are shifted by playback_min=1 in the wire format
        self.assertIn("9:footstep", stored)
        self.assertIn("23:jump",    stored)

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

        self.assertIn("9:footstep", manifest)
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
        self.assertIn(
            "9:footstep",
            content,
            "manifest entry should appear inside the FBX user-property "
            "block (frame 9 = Maya frame 10 minus playback_min=1)",
        )


if __name__ == "__main__":
    unittest.main()
