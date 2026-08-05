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
        self.low = cmds.polyCube(name="asset_low")[0]              # 8 points
        self.high = cmds.polySphere(
            name="asset_hi", subdivisionsX=16, subdivisionsY=16
        )[0]                                                        # 242 points
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
        self.assertEqual(
            os.path.dirname(high), os.path.dirname(payload.primary)
        )

    def test_hidden_high_poly_carries_its_geometry(self):
        # The load-bearing fact: Maya's FBX exporter writes hidden geometry
        # verbatim, so no unhide pass is needed to ship it.
        payload = self._produce("probe")
        self.assertEqual(_point_counts(payload.extras["high_poly_path"]), [242])

    def test_low_export_excludes_the_high_poly(self):
        payload = self._produce("probe")
        self.assertEqual(_point_counts(payload.primary), [8])

    def test_unticked_writes_no_high_poly_at_all(self):
        payload = self._produce("probe_off", high_poly=False)
        self.assertIsNone(payload.extras["high_poly_path"])
        self.assertFalse(
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


if __name__ == "__main__":
    unittest.main()
