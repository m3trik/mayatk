# !/usr/bin/python
# coding=utf-8
"""Scene Exporter: an FBX deliverable is self-contained.

The user's rule (2026-10-05): "all dependencies should be embedded into the
fbx. 0 sidecar." So an FBX deliverable carries its material textures (Embed
Media, pinned over a preset too), every file a scene record names (pythontk
``FbxMedia.embed_dependencies``) and the audio its events play, while the
texture passes stage their processed maps in scratch and the exporter keeps
its own records off the export folder: nothing ships beside the FBX. The
Unity half -- only the FBX copied into a project -- is unitytk's
``test_embedded_dependencies_integration``.
"""

import os
import struct
import unittest
import zlib
from unittest.mock import patch

import maya.cmds as cmds
import maya.mel as mel
import pythontk as ptk

from base_test import MayaTkTestCase
from mayatk.env_utils.scene_exporter._scene_exporter import SceneExporter


def png_bytes(width: int = 4, height: int = 4) -> bytes:
    """A small RGB PNG, written without an imaging library."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    rows = b"".join(b"\x00" + b"\x80\x40\x20" * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


class TestSelfContainedFbx(MayaTkTestCase):
    """What an FBX deliverable carries, and what is left beside it."""

    def setUp(self):
        super().setUp()
        store = ptk.TempArtifacts("mtk_self_contained_fbx", policy="scoped")
        self.addCleanup(store.cleanup)
        root = store.dir_path().replace("\\", "/")
        self.project = f"{root}/proj"
        self.images = f"{self.project}/sourceimages"
        self.out = f"{root}/out"
        for folder in (f"{self.project}/scenes", self.images, self.out):
            os.makedirs(folder, exist_ok=True)
        with open(f"{self.project}/workspace.mel", "w") as fh:
            fh.write("//Maya 2025 Project Definition\n")
        original = cmds.workspace(q=True, rootDirectory=True)
        self.addCleanup(lambda: cmds.workspace(original, openWorkspace=True))
        cmds.workspace(self.project, openWorkspace=True)
        cmds.file(rename=f"{self.project}/scenes/self_contained.ma")
        cmds.file(save=True, type="mayaAscii", force=True)
        self.addCleanup(cmds.file, new=True, force=True)
        cmds.loadPlugin("fbxmaya", quiet=True)
        # The plug-in's flags are session-sticky MEL state the tests below set
        # (a stubbed preset turns Cameras on): put them back for the next module.
        flags = {
            flag: bool(mel.eval(f"{flag} -q"))
            for flag in ("FBXExportEmbeddedTextures", "FBXExportCameras")
        }
        self.addCleanup(
            lambda: [
                mel.eval(f"{flag} -v {'true' if on else 'false'}")
                for flag, on in flags.items()
            ]
        )
        self.exporter = SceneExporter(log_level="WARNING")
        self.exporter.confirm = lambda question: False

    def _textured_cube(self):
        """A cube whose lambert reads a PNG from the project's sourceimages."""
        png = f"{self.images}/Crate_Base_color.png"
        with open(png, "wb") as fh:
            fh.write(png_bytes())
        cube = cmds.polyCube(name="Crate")[0]
        mat = cmds.shadingNode("lambert", asShader=True, name="Crate_mat")
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True)
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(cube, edit=True, forceElement=sg)
        tex = cmds.shadingNode("file", asTexture=True, name="Crate_file")
        cmds.setAttr(f"{tex}.fileTextureName", png, type="string")
        cmds.connectAttr(f"{tex}.outColor", f"{mat}.color", force=True)
        return cube, png

    def _export(self, objects, name, **tasks):
        ok = self.exporter.perform_export(
            export_dir=self.out,
            objects=objects,
            output_name=name,
            tasks={"export_data_node": True, **tasks},
        )
        return ok, os.path.join(self.out, f"{name}.fbx")

    def test_material_textures_ride_inside_an_fbx(self):
        """A loose-media FBX needed its maps copied beside it; an FBX
        deliverable now carries them like a GLB does (Embed Media on)."""
        cube, png = self._textured_cube()
        ok, fbx = self._export([cube], "plain")
        self.assertTrue(ok and os.path.isfile(fbx))
        embedded = {row["name"]: row["bytes"] for row in ptk.FbxMedia.embedded(fbx)}
        self.assertEqual(embedded.get(os.path.basename(png)), os.path.getsize(png))

    def test_nothing_ships_beside_the_fbx(self):
        """The deliverable is the FBX alone: its maps ride inside it, and the
        exporter's own record of what shipped (``.scene_data.json``) lives in
        the per-user store (``SceneDataSidecar.store_dir``)."""
        from mayatk.env_utils.hierarchy_sync.scene_data_sidecar import (
            SceneDataSidecar,
        )

        cube, _png = self._textured_cube()
        # The hierarchy check is what makes a run record its baseline.
        ok, fbx = self._export([cube], "alone", check_hierarchy_vs_existing_fbx=True)
        self.assertTrue(ok and os.path.isfile(fbx))
        self.assertEqual(os.listdir(self.out), [os.path.basename(fbx)])
        self.assertTrue(os.path.isfile(SceneDataSidecar.manifest_path_for(fbx)))

    def test_a_preset_cannot_leave_an_fbx_s_media_outside(self):
        """A preset owns content choices, never whether the deliverable carries
        its media: what Maya's own ``fbxexport`` preset leaves (Embed Media
        off) is pinned back on, the preset's content choices standing.

        ``run_tasks`` is stubbed to stop the run right after the preset."""
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        cube = cmds.polyCube(name="preset_probe")[0]
        preset = os.path.join(self.out, "p.fbxexportpreset")
        with open(preset, "w") as fh:
            fh.write("; preset\n")

        def _load(*_args, **_kwargs):
            mel.eval("FBXExportEmbeddedTextures -v false")
            mel.eval("FBXExportCameras -v true")

        with (
            patch.object(SceneExporter, "load_fbx_export_preset", side_effect=_load),
            patch.object(TaskManager, "run_tasks", return_value=False),
        ):
            self.exporter.perform_export(
                export_dir=self.out,
                objects=[cube],
                preset_file=preset,
                tasks={"output_format": "fbx", "smart_bake": False},
            )
        self.assertTrue(mel.eval("FBXExportEmbeddedTextures -q"))
        self.assertTrue(mel.eval("FBXExportCameras -q"), "a content choice was lost")

    def test_the_shipped_audio_rides_inside_under_its_event_names(self):
        """Unity matches an AudioClip to an ``audio`` event by name,
        and an event names its TRACK, not the track's file: the file goes in
        under the event's name, its extension kept."""
        import wave

        from mayatk.audio_utils._audio_utils import AudioUtils

        sound = f"{self.project}/sound"
        os.makedirs(sound, exist_ok=True)
        wav = f"{sound}/footstep_take3.wav"
        with wave.open(wav, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(22050)
            out.writeframes(b"\x00\x10" * 2205)
        cube = cmds.polyCube(name="Crate")[0]
        track = AudioUtils.add_clip(wav, 1, name="footstep")
        ok, fbx = self._export([cube], "audio")
        self.assertTrue(ok and os.path.isfile(fbx))
        embedded = {row["name"]: row["bytes"] for row in ptk.FbxMedia.embedded(fbx)}
        self.assertEqual(embedded.get(f"{track}.wav"), os.path.getsize(wav), embedded)

    def test_processed_textures_stage_in_scratch_for_an_fbx(self):
        """The texture passes' copies are read by the write and embedded: a
        ``textures/`` folder beside the deliverable was a sidecar. Only a USD
        layer, which references its maps, still stages them beside itself."""
        tm = self.exporter.task_manager
        fbx = os.path.join(self.out, "staged.fbx")
        mel.eval("FBXExportEmbeddedTextures -v false")  # whatever the session says
        tm.begin_run(ptk.ExportRun(export_path=fbx, output_format="fbx"))
        staging, temp = tm._texture_staging_dir("convert")
        self.assertTrue(temp)
        self.assertFalse(
            os.path.normcase(os.path.abspath(staging)).startswith(
                os.path.normcase(os.path.abspath(self.out))
            ),
            staging,
        )
        self.assertFalse(os.path.exists(os.path.join(self.out, "textures")))
        usd = os.path.join(self.out, "staged.usd")
        tm.begin_run(ptk.ExportRun(export_path=usd, output_format="usd"))
        staging, temp = tm._texture_staging_dir("convert")
        self.assertFalse(temp)
        self.assertEqual(os.path.dirname(staging), os.path.dirname(usd))


if __name__ == "__main__":
    unittest.main()
