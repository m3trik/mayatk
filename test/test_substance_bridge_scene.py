# !/usr/bin/python
# coding=utf-8
"""Scene-level tests for the Substance bridge's high-poly leg.

The Maya-runtime half of ``test_substance_bridge.py`` (which is
deliberately Maya-free and runs under the workspace venv). Everything
here needs a real scene and a real FBX exporter, because the behavior
being pinned is exactly what a mock would paper over:

* Maya's FBX exporter writes HIDDEN geometry verbatim -- which is why the
  bridge needs no unhide/re-hide pass, and why it can promise the scene
  is never modified.
* The high-poly set is read INSTEAD of the export scope, so hidden
  members never widen "Visible Only".
"""

import os
import re
import sys
import unittest

import maya.cmds as cmds

# Add test directory to path to import base_test
test_dir = os.path.dirname(__file__)
if test_dir not in sys.path:
    sys.path.append(test_dir)

import pythontk as ptk

from base_test import MayaTkTestCase
from mayatk.mat_utils.substance_bridge._substance_bridge import (
    SEND_TO,
    TARGET_AUTO,
    BakeSourceSet,
    SubstanceBridge,
)


def _point_counts(fbx_path):
    """Vertex-array lengths (in points) for every mesh in an ASCII FBX."""
    with open(fbx_path, "rb") as fh:
        raw = fh.read()
    return [int(n) // 3 for n in re.findall(rb"Vertices: \*(\d+)", raw)]


class TestBakeSourceSet(MayaTkTestCase):
    """The scene-resident membership store."""

    def setUp(self):
        super().setUp()
        self.low = cmds.polyCube(name="asset_low")[0]
        self.high = cmds.polySphere(name="asset_hi")[0]
        cmds.setAttr(f"{self.high}.visibility", 0)

    def test_define_and_read_back(self):
        BakeSourceSet.define([self.high])
        self.assertTrue(BakeSourceSet.exists())
        self.assertEqual(
            [n.split("|")[-1] for n in BakeSourceSet.members()], [self.high]
        )

    def test_define_replaces_rather_than_accumulates(self):
        BakeSourceSet.define([self.high])
        BakeSourceSet.define([self.low])
        self.assertEqual(
            [n.split("|")[-1] for n in BakeSourceSet.members()], [self.low]
        )

    def test_define_defaults_to_the_selection(self):
        cmds.select(self.high, replace=True)
        BakeSourceSet.define()
        self.assertEqual(
            [n.split("|")[-1] for n in BakeSourceSet.members()], [self.high]
        )

    def test_explicit_empty_list_clears_rather_than_capturing_selection(self):
        # ``define([])`` must mean "clear", never "capture whatever happens
        # to be selected" -- collapsing the two would silently adopt an
        # unrelated selection.
        BakeSourceSet.define([self.high])
        cmds.select(self.low, replace=True)
        BakeSourceSet.define([])
        self.assertFalse(BakeSourceSet.exists())
        self.assertEqual(BakeSourceSet.members(), [])

    def test_clear_keeps_the_geometry(self):
        BakeSourceSet.define([self.high])
        BakeSourceSet.clear()
        self.assertFalse(BakeSourceSet.exists())
        self.assertTrue(cmds.objExists(self.high))

    def test_members_drops_deleted_nodes(self):
        BakeSourceSet.define([self.high, self.low])
        cmds.delete(self.high)
        self.assertEqual(
            [n.split("|")[-1] for n in BakeSourceSet.members()], [self.low]
        )

    def test_no_set_reads_as_empty(self):
        self.assertFalse(BakeSourceSet.exists())
        self.assertEqual(BakeSourceSet.members(), [])


class TestHighPolyExport(MayaTkTestCase):
    """The produce-phase leg: what lands on disk, and what the scene
    looks like afterwards."""

    def setUp(self):
        super().setUp()
        self.low = cmds.polyCube(name="asset_low")[0]  # 8 points
        self.high = cmds.polySphere(
            name="asset_hi", subdivisionsX=16, subdivisionsY=16
        )[0]  # 242 points
        cmds.setAttr(f"{self.high}.visibility", 0)
        BakeSourceSet.define([self.high])

        # Scoped TempArtifacts, never a fixed temp path: the runner can
        # execute modules concurrently (--jobs), and two processes sharing
        # one hard-coded path would race on the very bytes under test.
        self.artifacts = ptk.TempArtifacts("mtk_substance_high", policy="scoped")
        self.out_dir = self.artifacts.dir_path()
        self.bridge = SubstanceBridge()

    def tearDown(self):
        self.artifacts.cleanup()
        super().tearDown()

    def _produce(self, name, high_poly=True, ascii_fbx=True):
        request = ptk.HandoffRequest(
            template="import",
            mode=SEND_TO,
            params={"PAINTER_HIGH_POLY": high_poly},
            extras={
                "output_dir": self.out_dir,
                "output_name": name,
                "painter_exe": None,
                "fbx_options": {"FBXExportInAscii": True} if ascii_fbx else None,
                "preset_file": None,
                "target": TARGET_AUTO,
            },
        )
        self.assertTrue(self.bridge._preflight([self.low], request))
        payload = self.bridge._produce([self.low], request)
        self.assertIsNotNone(payload)
        return payload

    # -- what lands on disk --------------------------------------------

    def test_high_poly_file_is_written_beside_the_mesh(self):
        payload = self._produce("probe")
        high = payload.extras["high_poly_path"]
        self.assertEqual(os.path.basename(high), "probe_source.fbx")
        self.assertTrue(os.path.isfile(high))
        self.assertEqual(os.path.dirname(high), os.path.dirname(payload.primary))

    def test_hidden_high_poly_carries_its_geometry(self):
        # The load-bearing fact: Maya's FBX exporter writes hidden geometry
        # verbatim, so no unhide pass is needed to ship it.
        payload = self._produce("probe")
        self.assertEqual(_point_counts(payload.extras["high_poly_path"]), [242])

    def test_low_export_excludes_the_high_poly(self):
        payload = self._produce("probe")
        self.assertEqual(_point_counts(payload.primary), [8])

    def test_a_params_flag_cannot_turn_the_bake_source_off(self):
        """The SET is the switch — there is no companion checkbox.

        This used to pair the set with an "Export Bake Source" tick, which
        had two ways to spell "off" and one silent failure mode: a set
        defined, the box left clear, which reads as a bug. Defining a set IS
        saying to ship it (same contract as the Marmoset bridge), so a stray
        params flag must not quietly countermand it. ``BakeSourceSet.clear()``
        is how you say no — see
        ``test_empty_set_is_a_warning_not_a_failure``.
        """
        payload = self._produce("probe_off", high_poly=False)
        self.assertIsNotNone(payload.extras["high_poly_path"])
        self.assertTrue(
            os.path.isfile(os.path.join(self.out_dir, "probe_off_source.fbx"))
        )

    def test_empty_set_is_a_warning_not_a_failure(self):
        # The main mesh is already on disk; an absent set must not sink it.
        BakeSourceSet.clear()
        payload = self._produce("probe_empty")
        self.assertIsNone(payload.extras["high_poly_path"])
        self.assertTrue(os.path.isfile(payload.primary))

    # -- what the scene looks like afterwards ---------------------------

    def test_scene_is_not_modified(self):
        visibility = cmds.getAttr(f"{self.high}.visibility")
        cmds.select(self.low, replace=True)
        selection = cmds.ls(selection=True, long=True)

        self._produce("probe")

        self.assertEqual(cmds.getAttr(f"{self.high}.visibility"), visibility)
        self.assertEqual(cmds.ls(selection=True, long=True), selection)
        self.assertEqual(
            [n.split("|")[-1] for n in BakeSourceSet.members()], [self.high]
        )

    def test_locked_visibility_does_not_break_the_export(self):
        # The case that rules out a force-visible pass: setAttr raises on a
        # locked plug. The export must not care.
        cmds.setAttr(f"{self.high}.visibility", lock=True)
        self.addCleanup(cmds.setAttr, f"{self.high}.visibility", lock=False)
        payload = self._produce("probe_locked")
        self.assertEqual(_point_counts(payload.extras["high_poly_path"]), [242])
        self.assertTrue(cmds.getAttr(f"{self.high}.visibility", lock=True))

    def test_high_poly_never_widens_the_visible_scope(self):
        from mayatk.display_utils._display_utils import DisplayUtils

        self._produce("probe")
        visible = (
            DisplayUtils.get_visible_geometry(
                shapes=True, inherit_parent_visibility=True
            )
            or []
        )
        self.assertFalse(any(self.high in node for node in visible))


class TestHandoffLocations(MayaTkTestCase):
    """Where a send with a blank Output Dir puts things: the mesh Painter's
    project is built from (and a reimport overwrites) in the scene project's
    ``cache/substance``; the manifest and staged copies in temp scratch --
    never the scene folder, and never a temp mesh recorded on the scene.
    Added: 2026-10-13"""

    def setUp(self):
        super().setUp()
        import tempfile

        # Outside the temp root (the run's sandbox puts tempfile's folder
        # under it), as a real project is: a mesh under the temp root is
        # never recorded on the scene.
        os.makedirs(self.TEMP_TESTS, exist_ok=True)
        self.root = os.path.realpath(
            tempfile.mkdtemp(prefix="substance_proj_", dir=self.TEMP_TESTS)
        )
        ptk.Workspace.create(self.root, create_dirs=False)
        os.makedirs(os.path.join(self.root, "scenes"))
        cmds.file(rename=os.path.join(self.root, "scenes", "shot.ma"))
        self.mesh = cmds.polyCube(name="paint_me")[0]
        self.bridge = SubstanceBridge()
        self.bridge.logger.setLevel("CRITICAL")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)
        super().tearDown()

    def _produce(self, output_name="shot"):
        extras = {"painter_exe": None, "target": TARGET_AUTO}
        if output_name:
            extras["output_name"] = output_name
        request = ptk.HandoffRequest(
            template="import", mode=SEND_TO, params={}, extras=extras
        )
        self.assertTrue(self.bridge._preflight([self.mesh], request))
        payload = self.bridge._produce([self.mesh], request)
        self.assertIsNotNone(payload)
        return payload

    def _under(self, path, folder):
        return os.path.normcase(os.path.realpath(path)).startswith(
            os.path.normcase(os.path.realpath(folder))
        )

    def test_the_mesh_goes_to_the_project_cache_and_the_rest_to_temp(self):
        payload = self._produce()
        cache = os.path.join(self.root, "cache", "substance")
        self.assertEqual(
            os.path.normcase(os.path.dirname(os.path.realpath(payload.primary))),
            os.path.normcase(cache),
        )
        self.assertTrue(
            self._under(payload.extras["manifest_path"], ptk.TempArtifacts.root()),
            payload.extras["manifest_path"],
        )
        self.assertEqual(
            os.path.normcase(os.path.normpath(SubstanceBridge._recorded_export_path())),
            os.path.normcase(os.path.normpath(payload.primary)),
        )
        self.assertEqual(
            sorted(os.listdir(os.path.join(self.root, "scenes"))),
            [],
            "nothing lands beside the scene",
        )

    def test_two_same_named_scenes_keep_their_own_cached_mesh(self):
        """Pre-release review 2026-10-13: ``cache/substance/<scene>.fbx`` was
        keyed by the scene's NAME, so ``scenes/a/shot.ma`` and
        ``scenes/b/shot.ma`` overwrote the mesh each other's Painter project
        is built from."""
        first = self._produce(output_name=None).primary
        os.makedirs(os.path.join(self.root, "scenes", "b"))
        cmds.file(rename=os.path.join(self.root, "scenes", "b", "shot.ma"))
        second = self._produce(output_name=None).primary

        self.assertNotEqual(os.path.normcase(first), os.path.normcase(second))
        for path in (first, second):
            self.assertRegex(os.path.basename(path), r"^shot_[0-9a-f]{8}\.\w+$")
        cmds.file(rename=os.path.join(self.root, "scenes", "shot.ma"))
        self.assertEqual(
            os.path.normcase(self._produce(output_name=None).primary),
            os.path.normcase(first),
            "one scene, one cached mesh",
        )

    def test_with_no_project_the_temp_mesh_is_never_recorded(self):
        from unittest import mock

        cmds.fileInfo(SubstanceBridge.EXPORT_RECORD_KEY, "")
        with mock.patch.object(
            type(self.bridge), "_scene_workspace", return_value=None
        ):
            payload = self._produce()
        self.assertTrue(self._under(payload.primary, ptk.TempArtifacts.root()))
        self.assertIsNone(SubstanceBridge._recorded_export_path())


if __name__ == "__main__":
    unittest.main()
