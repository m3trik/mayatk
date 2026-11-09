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


def _cue_set(manifest: str, fps: float = 24.0):
    """Parse a v3 record into a comparable ``{(clip, sound, time, offset,
    stop)}`` set, the seconds read back as frames at *fps*."""
    payload = json.loads(manifest)
    return {
        (
            c["clip"],
            c["sound"],
            round(c["time"] * fps, 3),
            round(c["offset"] * fps, 3),
            round(c["stop"] * fps, 3),
        )
        for c in payload["cues"]
    }


def _write_wav(path: str, seconds: float = 0.5, rate: int = 8000) -> str:
    """A silent mono 16-bit WAV of *seconds*."""
    import wave

    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(2) * int(rate * seconds))
    return path


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
        # Something the file animates, keyed 1-60: a shotless file's one take
        # holds those keys, so its cues count from frame 1 (an FBX with no
        # animation has no clip to cue).
        cmds.playbackOptions(min=1, max=60)
        mover = cmds.polyCube(name="mover")[0]
        cmds.setKeyframe(mover, attribute="translateX", time=1, value=0)
        cmds.setKeyframe(mover, attribute="translateX", time=60, value=5)
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
        # v3: cues per clip in seconds (no takes published: the file's one
        # take, from its first key at 1), each stopped at its OFF key, and no
        # frame or rate leaves the DCC.
        payload = json.loads(stored)
        self.assertEqual(payload["version"], AudioClips.MANIFEST_VERSION)
        self.assertEqual(set(payload), {"version", "cues"})
        self.assertEqual(
            _cue_set(stored),
            {("", "footstep", 9, 0, 14), ("", "jump", 23, 0, 27)},
        )

    def _publish_clips(self, *clips):
        """Declare the scene's shots (``(name, start, end)``) in its shot
        store: one take each, the take list an export scopes against
        (``ShotStore.declared_takes``)."""
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore()
        ShotStore.set_active(store)
        self.addCleanup(ShotStore.clear_active)
        for name, start, end in clips:
            store.define_shot(name, start, end)

    def test_prepare_for_export_scopes_events_to_takes(self):
        """With the shots published, each sound is cut per clip, counted from
        the clip's start -- where the imported AnimationClip counts from; a
        sound across a cut stops at it and plays on in the next clip from its
        offset. Sounds no clip plays are dropped."""
        self._seed_tracks()  # footstep 10-15, jump 24-28
        audio_utils.write_key("stray", frame=59, value=1)  # outside both takes
        audio_utils.write_key("hum", frame=18, value=1)  # across the cut at 21
        audio_utils.write_key("hum", frame=30, value=0)
        self._publish_clips(("Intro", 1, 20), ("Outro", 21, 50))

        with self.assertLogs(AudioClips.logger, level="WARNING") as logs:
            manifest = AudioClips.prepare_for_export()
        self.assertEqual(
            _cue_set(manifest),
            {
                ("Intro", "footstep", 9, 0, 14),
                ("Intro", "hum", 17, 0, 19),
                ("Outro", "hum", 0, 3, 9),
                ("Outro", "jump", 3, 0, 7),
            },
        )
        self.assertTrue(any("stray@59" in line for line in logs.output), logs.output)

    def test_the_takes_are_the_stores_not_the_records(self):
        """The ``animation`` record states no frames (v3): a record left from
        an export before it, ranges and all, declares no take. With no shot
        in the store the cues are the file's one take's. Added: 2026-11-05
        """
        from mayatk.anim_utils.shots._shots import ShotStore
        from mayatk.node_utils.data_nodes import DataNodes

        self._seed_tracks()
        ShotStore.set_active(ShotStore())
        self.addCleanup(ShotStore.clear_active)
        DataNodes.write(
            ptk.Scope.DELIVERABLE,
            ptk.SceneRecords.SHOTS.key,
            json.dumps(
                {"version": 1, "fps": 24.0, "shots": [{"clip": "Intro", "start": 1, "end": 30}]}
            ),
        )

        manifest = AudioClips.prepare_for_export()
        self.assertEqual(
            _cue_set(manifest),
            {("", "footstep", 9, 0, 14), ("", "jump", 23, 0, 27)},
        )

    def test_a_shotless_take_counts_from_the_window_its_file_declares(self):
        """Before a write, a shotless file's one take is the window Maya's
        writer declares by itself: the playback range (measured on Maya 2025 /
        FBX 2020.3.6, 2026-11-05: keys 10-100 under 1-120 declared 1-120, the
        bake on or off), the bake flags and the plug-in unread. Once the file
        is written its window is measured, fitted to the keys it holds
        (``ptk.FbxMedia.fit_take_windows``), and the cues are cut over that
        (``ctx.take_window``). Counted from a predicted span, a raw File >
        Export declared 1-120 while its cues counted from the keys' 10: nine
        frames early in Unity. Added: 2026-10-20; rule 2026-11-05
        """
        self._seed_tracks()  # footstep 10-15, jump 24-28; keys 1-60
        cmds.playbackOptions(min=4, max=60)
        self.addCleanup(mel.eval, "FBXResetExport")
        mel.eval("FBXResetExport")
        mel.eval("FBXExportBakeComplexAnimation -v true")
        mel.eval("FBXExportBakeComplexStart -v -5")
        mel.eval("FBXExportBakeResampleAnimation -v true")
        declared = AudioClips.export_record(ptk.ExportContext())
        self.assertEqual(
            _cue_set(declared.text),
            {("", "footstep", 6, 0, 11), ("", "jump", 20, 0, 24)},
            "unmeasured: the playback range the writer declares, whatever it bakes",
        )
        measured = AudioClips.export_record(ptk.ExportContext(take_window=(-5.0, 60.0)))
        self.assertEqual(
            _cue_set(measured.text),
            {("", "footstep", 15, 0, 20), ("", "jump", 29, 0, 33)},
            "measured: the window the written file declares",
        )
        self.assertIsNone(
            AudioClips.export_record(ptk.ExportContext(animated=False)),
            "a write that carries no animation has no clip to cue",
        )

    def test_the_authoring_publish_loads_no_plug_in(self):
        """An authoring publish writes nothing: it reads the tracks, the
        shots and, shotless, the playback range -- never the FBX plug-in's
        settings, and it never loads the plug-in to ask (after every track
        edit). Added: 2026-11-05"""
        self._seed_tracks()
        try:
            cmds.unloadPlugin("fbxmaya", force=True)
        except RuntimeError:
            self.skipTest("the FBX plug-in will not unload in this session")
        self.addCleanup(cmds.loadPlugin, "fbxmaya", quiet=True)
        manifest = AudioClips.prepare_for_export()
        self.assertTrue(manifest)
        self.assertFalse(
            cmds.pluginInfo("fbxmaya", query=True, loaded=True),
            "the authoring publish loaded the FBX plug-in",
        )
        from mayatk.env_utils.fbx_utils import FbxUtils

        self.assertIsNone(
            FbxUtils.export_context().animated,
            "a context outside an FBX write states no Animation include group",
        )
        self.assertFalse(cmds.pluginInfo("fbxmaya", query=True, loaded=True))

    def test_a_sound_starting_before_the_take_plays_from_its_offset(self):
        """A sound that starts before the shotless take does plays from its
        offset into the sound; one wholly outside it is dropped, and the
        warning says where. Added: 2026-10-20; cut 2026-11-05
        """
        self._seed_tracks()  # keys 1-60
        audio_utils.write_key("early", frame=-5, value=1)
        audio_utils.write_key("early", frame=5, value=0)
        audio_utils.write_key("gone", frame=-20, value=1)
        audio_utils.write_key("gone", frame=-10, value=0)
        with self.assertNoLogs(AudioClips.logger, level="WARNING"):
            AudioClips.export_record(ptk.ExportContext())  # before a write: not yet dropped
        with self.assertLogs(AudioClips.logger, level="WARNING") as logs:
            record = AudioClips.export_record(ptk.ExportContext(take_window=(1.0, 60.0)))
        self.assertIn(("", "early", 0, 6, 4), _cue_set(record.text))
        self.assertNotIn("gone", {c["sound"] for c in record.payload["cues"]})
        self.assertTrue(
            any(
                "gone@-20" in line and "one take" in line and "shot" not in line
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

        self.assertIn(("", "footstep", 9, 0, 14), _cue_set(manifest))
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

    def test_the_record_leaves_its_digests_to_the_dependency_pass(self):
        """``media`` -- each embedded clip's SHA-256, what Unity compares a
        same-named clip against before it reuses one -- is the FBX dependency
        pass's to write, from the bytes it embedded
        (``ptk.FbxMedia.embed_dependencies``). Written here from the source
        files, it named audio a raw File > Export or the Game Exporter never
        embedded, and Unity bound nothing where a library clip had bound
        (review, 2026-10-31). Added: 2026-10-31"""
        self._seed_tracks()
        wav = self.temp_path("footstep_take3.wav")
        with open(wav, "wb") as fh:
            fh.write(b"footstep bytes")
        audio_utils.set_path("footstep", wav)

        record = AudioClips.export_record(ptk.ExportContext())
        self.assertIsNotNone(record)
        self.assertNotIn("media", record.payload)
        self.assertEqual(
            ptk.SceneRecords.problems(ptk.SceneRecords.AUDIO, record.payload), []
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


class TestAudioClipsRealExport(MayaTkTestCase):
    """The cues as a real Scene Exporter write ships them: the FBX's own take
    windows and key extents (``ptk.FbxFile``) against its ``audio`` record,
    and the GLB carrying that record as it stands. Each cue's time, added to
    where its clip's take starts in the file, lands on the frame the sound
    starts on in the scene. Added: 2026-11-05"""

    def setUp(self):
        super().setUp()
        try:
            cmds.loadPlugin("fbxmaya", quiet=True)
        except RuntimeError:
            self.skipTest("FBX plugin not available")
        artifacts = ptk.TempArtifacts("audio_clips_export", policy="scoped")
        self.addCleanup(artifacts.cleanup)
        self.out = artifacts.dir_path()
        self.fps = audio_utils.get_fps()

    def _export(self, name, objects, **tasks):
        from mayatk.env_utils.scene_exporter._scene_exporter import SceneExporter

        exporter = SceneExporter(log_level="WARNING")
        exporter.confirm = lambda question: False
        written = exporter.perform_export(
            export_dir=self.out,
            objects=objects,
            output_name=name,
            tasks={"output_format": "fbx_glb", "export_data_node": True, **tasks},
        )
        self.assertTrue(written, f"the {name} export wrote nothing")
        fbx = os.path.join(self.out, f"{name}.fbx")
        glb = os.path.join(self.out, f"{name}.glb")
        record = ptk.FbxMedia.record(fbx, ptk.SceneRecords.AUDIO.key)
        self.assertEqual(ptk.SceneRecords.problems(ptk.SceneRecords.AUDIO, record), [])
        with ptk.MeshConvert.open_glb(glb) as edit:
            carried = (edit.gltf.get("extras") or {}).get(ptk.SceneRecords.AUDIO.key)
        # As it stands, less 'media': the digests are of the sound files the
        # FBX's dependency pass embeds, and a GLB embeds none -- a file
        # written without that pass carries no 'media' (the record's rule).
        self.assertEqual(
            carried,
            {k: v for k, v in record.items() if k != "media"},
            "the GLB carries the FBX's record as it stands",
        )
        return ptk.FbxFile.load(fbx, span_arrays=("KeyTime",)), record

    def _sound(self, track, start, stop):
        wav = _write_wav(self.temp_path(f"{track}.wav"), seconds=(stop - start) / self.fps)
        audio_utils.set_path(track, wav)
        audio_utils.write_key(track, frame=start, value=1)
        audio_utils.write_key(track, frame=stop, value=0)

    def test_a_shotless_export_cues_from_where_its_take_starts(self):
        """The S2 case: keys 0-100, the scene range 20-80, Bake Range "Scene
        Animation Range", a sound at 50-60. The file's one take holds the
        keys, 0-100, so the cue is 50 frames in -- counted from the bake
        range's 20 it played 20 frames early."""
        box = cmds.polyCube(name="box")[0]
        cmds.setKeyframe(box, attribute="translateX", time=0, value=0)
        cmds.setKeyframe(box, attribute="translateX", time=100, value=10)
        cmds.playbackOptions(min=20, max=80, animationStartTime=20, animationEndTime=80)
        self._sound("hit", 50, 60)

        fbx, record = self._export("s2", [box], set_bake_animation_range="scene")
        spans = fbx.take_spans()
        self.assertEqual(len(spans), 1, spans)
        self.assertEqual(fbx.take_windows(), spans, "the take declares what it holds")
        (first, _last), = spans.values()
        self.assertAlmostEqual(first * self.fps, 0.0, places=3, msg="the take holds the keys")
        (cue,) = record["cues"]
        self.assertEqual((cue["clip"], cue["sound"]), ("", "hit.wav"))
        self.assertAlmostEqual((first + cue["time"]) * self.fps, 50.0, places=3)
        self.assertAlmostEqual((first + cue["stop"]) * self.fps, 60.0, places=3)
        self.assertEqual(cue["offset"], 0.0)
        self.assertEqual(sorted(record.get("media") or {}), ["hit.wav"])

    def _frames(self, fbx):
        """``(declared windows, key spans)`` of *fbx*'s takes, in frames."""
        frames = lambda spans: {
            take: tuple(round(t * self.fps, 3) for t in span) for take, span in spans.items()
        }
        return frames(fbx.take_windows()), frames(fbx.take_spans())

    def _cue_frames(self, path):
        """Each cue of *path*'s record as ``(clip, start, offset, stop)``
        frames, counted from where its clip's take window starts in the file."""
        fbx = ptk.FbxFile.load(path, span_arrays=("KeyTime",))
        windows = fbx.take_windows()
        record = ptk.FbxMedia.record(path, ptk.SceneRecords.AUDIO.key)
        self.assertEqual(ptk.SceneRecords.problems(ptk.SceneRecords.AUDIO, record), [])
        return fbx, [
            (
                c["clip"],
                round((windows[c["clip"] or "Take 001"][0] + c["time"]) * self.fps, 3),
                round(c["offset"] * self.fps, 3),
                round((windows[c["clip"] or "Take 001"][0] + c["stop"]) * self.fps, 3),
            )
            for c in record["cues"]
        ]

    def _hooked(self):
        """The session hook on, as creating a track turns it on."""
        from mayatk.env_utils.fbx_utils import FbxUtils

        FbxUtils.enable_export_producer(ptk.SceneRecords.AUDIO)
        self.addCleanup(FbxUtils.disable_export_producer, ptk.SceneRecords.AUDIO)
        self.addCleanup(mel.eval, "FBXResetExport")
        mel.eval("FBXResetExport")

    def test_a_raw_shotless_export_declares_what_it_holds_and_cues_from_it(self):
        """File > Export Selection (any ``cmds.file`` write), the session hook
        on, no shots: keys 10-100 under a 1-120 playback range, a sound at
        50-60. Maya's writer declares 1-120, which Unity cuts its clip by,
        while the cues counted from the keys' 10: nine frames early. The
        written file's window is measured and fitted to what it holds, and the
        cue counts from it. Added: 2026-11-05"""
        from mayatk.node_utils.data_nodes import DataNodes

        self._hooked()
        box = cmds.polyCube(name="box")[0]
        cmds.setKeyframe(box, attribute="translateX", time=10, value=0)
        cmds.setKeyframe(box, attribute="translateX", time=100, value=10)
        cmds.playbackOptions(min=1, max=120, animationStartTime=1, animationEndTime=120)
        self._sound("hit", 50, 60)
        DataNodes.ensure_export()
        path = os.path.join(self.out, "raw.fbx")
        cmds.select([box, DataNodes.EXPORT], replace=True)
        cmds.file(path, force=True, options="v=0;", type="FBX export", exportSelected=True)

        fbx, cues = self._cue_frames(path)
        windows, spans = self._frames(fbx)
        self.assertEqual(spans, {"Take 001": (10.0, 100.0)}, "the take holds the keys")
        self.assertEqual(windows, spans, "and declares what it holds")
        self.assertEqual(cues, [("", 50.0, 0.0, 60.0)])
        self.assertEqual(
            tuple(cmds.playbackOptions(q=True, **{f: True}) for f in ("min", "max")),
            (1.0, 120.0),
            "the scene's range is never moved",
        )

    def test_an_animated_ancestor_the_write_carries_along_sets_the_window(self):
        """The plug-in ships the parents of what it is handed: a child
        selected under a group keyed 0-100 (playback 20-80) ships the group's
        keys, which a span predicted from the selection's subtree missed. The
        file says what it holds. Added: 2026-11-05"""
        from mayatk.env_utils.fbx_utils import FbxUtils
        from mayatk.node_utils.data_nodes import DataNodes

        self._hooked()
        group = cmds.group(empty=True, name="rig_root")
        cmds.setKeyframe(group, attribute="translateX", time=0, value=0)
        cmds.setKeyframe(group, attribute="translateX", time=100, value=10)
        leaf = cmds.ls(cmds.parent(cmds.polyCube(name="leaf")[0], group)[0], long=True)[0]
        cmds.playbackOptions(min=20, max=80, animationStartTime=20, animationEndTime=80)
        # Before the playback range: the writer's own window leaves it out, the
        # group's keys hold it.
        self._sound("hit", 5, 15)
        DataNodes.ensure_export()

        def raw(path):
            cmds.select([leaf, DataNodes.EXPORT], replace=True)
            cmds.file(path, force=True, options="v=0;", type="FBX export", exportSelected=True)

        for name, write in (
            ("exported", lambda path: FbxUtils.export(path, [leaf, DataNodes.EXPORT])),
            ("raw", raw),
        ):
            with self.subTest(name):
                path = os.path.join(self.out, f"ancestor_{name}.fbx")
                write(path)
                fbx, cues = self._cue_frames(path)
                windows, spans = self._frames(fbx)
                self.assertEqual(spans, {"Take 001": (0.0, 100.0)}, "the group's keys ship")
                self.assertEqual(windows, spans)
                self.assertEqual(cues, [("", 5.0, 0.0, 15.0)])

    def test_a_shot_export_cuts_each_sound_at_its_takes(self):
        """One take per shot, named after it: a sound across the cut is a cue
        in each, counted from where each take starts in the file and stopped
        at the cut; the next plays it on from its offset."""
        from mayatk.anim_utils.shots._shots import ShotStore

        mover = cmds.polyCube(name="mover")[0]
        cmds.setKeyframe(mover, attribute="translateX", time=1, value=0)
        cmds.setKeyframe(mover, attribute="translateX", time=40, value=10)
        store = ShotStore()
        ShotStore.set_active(store)
        self.addCleanup(ShotStore.clear_active)
        store.define_shot("ShotA", 1, 20, objects=[mover])
        store.define_shot("ShotB", 21, 40, objects=[mover])
        self._sound("hum", 15, 30)

        fbx, record = self._export("cut", [mover])
        windows = fbx.take_windows()
        self.assertEqual(sorted(windows), ["ShotA", "ShotB"])
        got = [
            (
                c["clip"],
                round((windows[c["clip"]][0] + c["time"]) * self.fps, 3),
                round(c["offset"] * self.fps, 3),
                round((windows[c["clip"]][0] + c["stop"]) * self.fps, 3),
            )
            for c in record["cues"]
        ]
        self.assertEqual(got, [("ShotA", 15, 0, 20), ("ShotB", 21, 6, 30)])
        spans = fbx.take_spans()
        for clip, (start, _end) in windows.items():
            self.assertAlmostEqual(spans[clip][0], start, places=4, msg=clip)


if __name__ == "__main__":
    unittest.main()
