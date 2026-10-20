# !/usr/bin/python
# coding=utf-8
"""Panel tests for ``texture_combine.ui`` + TextureCombineSlots.

GUI-only (registered in ``run_tests.GUI_REQUIRED``): the panel hosts a uitk
``TableWidget``, and Qt table classes hard-crash mayapy in batch.

Where ``test_texture_combine.py`` covers the engine, this drives the panel the
way a user does: the mode switch shows each mode's groups, the Sources table
follows the selection and re-plans on a Scale edit, Combine / Update / the
picker's verbs run end to end, and Transfer mode transfers.
"""

import os
import unittest
from unittest import mock

import numpy as np
import maya.cmds as cmds
import pythontk as ptk
from PIL import Image

from base_test import MayaTkTestCase
from mayatk.ui_utils.maya_ui_handler import MayaUiHandler
from mayatk.uv_utils.texture_combine.texture_combine import TextureCombine
from mayatk.uv_utils.texture_transfer import TextureTransfer


class _PanelCase(MayaTkTestCase):
    @classmethod
    def setUpClass(cls):
        cls.ui = MayaUiHandler.instance().get("texture_combine")
        cls.slots = cls.ui.slots
        cls.sb = cls.slots.sb
        cls.sb.QtWidgets.QApplication.processEvents()  # the deferred wiring

    def setUp(self):
        super().setUp()
        self._artifacts = ptk.TempArtifacts("texture_combine_panel", policy="scoped")
        self.addCleanup(self._artifacts.cleanup)
        self.tmp = self._artifacts.dir_path()
        # Reports are read back, not shown.
        self.boxes = []
        patcher = mock.patch.object(
            self.sb,
            "message_box",
            side_effect=lambda text, *a, **k: self.boxes.append(text),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ui.t_output.setText(os.path.join(self.tmp, "out").replace("\\", "/"))
        self.ui.t_name.clear()
        self.ui.cmb_atlas.setCurrentIndex(0)
        self.ui.cmb_quality.setCurrentIndex(0)
        self._mode("combine")
        self.slots._refresh_sets(select="")
        self.slots._on_set()

    def _mode(self, mode):
        self.ui.cmb_mode.setCurrentIndex(self.ui.cmb_mode.findData(mode))
        self.sb.QtWidgets.QApplication.processEvents()

    def _material(self, name, rgb):
        path = os.path.join(self.tmp, f"{name}.png").replace("\\", "/")
        Image.fromarray(np.full((16, 16, 3), rgb, np.uint8)).save(path)
        mat = cmds.shadingNode("lambert", asShader=True, name=name)
        sg = cmds.sets(
            name=f"{name}SG", renderable=True, noSurfaceShader=True, empty=True
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader")
        f = cmds.shadingNode("file", asTexture=True, name=f"{name}_file")
        cmds.setAttr(f"{f}.fileTextureName", path, type="string")
        cmds.connectAttr(f"{f}.outColor", f"{mat}.color")
        return sg

    def _scene(self):
        a = cmds.polyPlane(name="pa", sx=1, sy=1, ch=False)[0]
        b = cmds.polyPlane(name="pb", sx=1, sy=1, ch=False)[0]
        cmds.move(2, 0, 0, b)
        cmds.sets(a, e=True, forceElement=self._material("matA", (255, 0, 0)))
        cmds.sets(b, e=True, forceElement=self._material("matB", (0, 0, 255)))
        cmds.select([a, b], replace=True)
        self.slots._follow_selection(force=True)
        return cmds.ls(a, long=True)[0], cmds.ls(b, long=True)[0]

    def _table(self):
        t = self.ui.tbl_sources
        return [
            [
                t.item(r, c).text() if t.item(r, c) else ""
                for c in range(t.columnCount())
            ]
            for r in range(t.rowCount())
        ]


class TestSurface(_PanelCase):
    def test_widgets_resolve(self):
        for name in (
            "header", "cmb_mode", "cmb_set", "tbl_sources", "b001", "b002",
            "cmb_sizing", "cmb_normalize", "cmb_atlas", "s_spacing", "chk_rotate",
            "chk_lightmaps", "cmb_tt_transfer", "cmb_tt_source", "cmb_tt_targets",
            "t_tt_src_uvset", "t_tt_dst_uvset", "cmb_tt_resolution",
            "chk_tt_lightmaps", "t_name", "t_output", "cmb_quality", "s_padding",
            "chk_assign", "t_affix", "cmb_shader", "b000", "footer",
        ):  # fmt: skip
            self.assertIsNotNone(getattr(self.ui, name, None), name)
        self.assertEqual(type(self.ui.tbl_sources).__name__, "TableWidget")

    def test_mode_switch_shows_each_modes_groups(self):
        self._mode("combine")
        self.assertFalse(self.ui.grp_sources.isHidden())
        self.assertTrue(self.ui.grp_transfer.isHidden())
        self.assertFalse(self.ui.grp_output.isHidden())
        self.assertTrue(self.ui.chk_assign.isHidden())
        self.assertEqual(self.ui.b000.text(), "Combine")
        self._mode("transfer")
        self.assertTrue(self.ui.grp_sources.isHidden())
        self.assertTrue(self.ui.cmb_set.isHidden())
        self.assertFalse(self.ui.grp_transfer.isHidden())
        self.assertFalse(self.ui.grp_output.isHidden())
        self.assertFalse(self.ui.chk_assign.isHidden())
        self.assertEqual(self.ui.b000.text(), "Transfer")

    def test_combine_re_enables_the_rows_transfer_greyed(self):
        """Transfer: UV Set greys the shared Output / Material rows (no maps
        are written); back in Combine, which always writes and assigns, they
        stayed grey."""
        self._mode("transfer")
        for combo, data in (
            (self.ui.cmb_tt_source, "first"),
            (self.ui.cmb_tt_transfer, "uvs"),
        ):
            combo.setCurrentIndex(combo.findData(data))
        self.assertFalse(self.ui.t_output.isEnabled())
        self._mode("combine")
        for name in ("t_output", "cmb_quality", "s_padding", "t_affix", "cmb_shader"):
            self.assertTrue(getattr(self.ui, name).isEnabled(), name)

    def test_normalize_enables_only_for_proportional(self):
        sizing = self.ui.cmb_sizing
        sizing.setCurrentIndex(sizing.findData("density"))
        self.assertFalse(self.ui.cmb_normalize.isEnabled())
        sizing.setCurrentIndex(sizing.findData("proportional"))
        self.assertTrue(self.ui.cmb_normalize.isEnabled())
        sizing.setCurrentIndex(sizing.findData("density"))

    def test_parse_scale(self):
        parse = self.slots.parse_scale
        self.assertAlmostEqual(parse("1/3"), 1 / 3)
        self.assertAlmostEqual(parse("33%"), 0.33)
        self.assertAlmostEqual(parse("0,5"), 0.5)
        self.assertIsNone(parse("abc"))
        self.assertIsNone(parse("-1"))


class TestCombineMode(_PanelCase):
    def test_table_follows_selection_and_replans_on_a_scale_edit(self):
        self._scene()
        rows = self._table()
        self.assertEqual([r[0] for r in rows], ["matA", "matB"])
        self.assertEqual(rows[0][3], "50%")
        # Type 1/3 into matB's Scale cell, the way a user edits it.
        self.ui.tbl_sources.item(1, self.slots.SCALE_COL).setText("1/3")
        rows = self._table()
        self.assertEqual(rows[1][2], "0.33")
        self.assertEqual(rows[1][3], "10%")  # (1/3)^2 of matA's area
        self.assertIn("2 mesh(es)", self.ui.footer.text())

    def test_combine_update_and_restore_from_the_picker(self):
        a, b = self._scene()
        self.ui.t_name.setText("kit")
        self.slots.b000()
        self.assertEqual(TextureCombine.names(), ["kit"])
        self.assertEqual(self.slots._current_set(), "kit")
        self.assertEqual(self.ui.b000.text(), "Update")
        self.assertFalse(self.ui.t_name.isEnabled())
        # The stored set re-opens with its ORIGINAL materials in the table.
        self.assertEqual([r[0] for r in self._table()], ["matA", "matB"])
        mats = {TextureTransfer.face_materials(o)[0][0] for o in (a, b)}
        self.assertEqual(len(mats), 1)

        self.ui.tbl_sources.item(1, self.slots.SCALE_COL).setText("0.5")
        self.slots.b000()  # Update: re-pack with the edit
        span = lambda o: np.ptp(
            np.asarray(cmds.polyEditUV(f"{o}.map[*]", q=True)).reshape(-1, 2), axis=0
        ).max()  # noqa: E731
        self.assertAlmostEqual(span(b) / span(a), 0.5, delta=0.03)

        self.slots.restore_set()
        self.assertEqual(TextureCombine.names(), [])
        self.assertEqual(self.slots._current_set(), "")
        self.assertEqual(TextureTransfer.face_materials(a)[0], ["matA"])

    def test_auto_spacing_reopens_as_auto(self):
        """A combine made with Auto spacing re-opens as Auto (not the count it
        resolved to), so an Update at another atlas size re-derives the gutter."""
        self._scene()
        self.ui.s_spacing.setValue(-1)
        self.ui.t_name.setText("kit")
        self.slots.b000()
        self.assertEqual(self.slots._current_set(), "kit")
        self.assertIsNone(TextureCombine.entry("kit")["settings"]["padding"])
        self.assertEqual(self.ui.s_spacing.value(), -1)

    def test_combine_reads_the_meshes_at_run_time_not_the_table_cache(self):
        """A UV edited after the table read the selection is what gets packed
        (and so what Restore brings back)."""
        a, b = self._scene()
        cmds.polyEditUV(f"{a}.map[0]", uValue=0.05, vValue=0.0)  # after the read
        edited = cmds.polyEditUV(f"{a}.map[*]", q=True)
        self.ui.t_name.setText("kit")
        self.slots.b000()
        self.slots.restore_set()
        np.testing.assert_allclose(
            cmds.polyEditUV(f"{a}.map[*]", q=True), edited, atol=1e-6
        )

    def test_mesh_override_row(self):
        a, b = self._scene()
        cmds.select(b, replace=True)
        self.slots.b001()
        rows = self._table()
        self.assertEqual(rows[-1][0].strip(), "mesh: pb")
        self.ui.tbl_sources.setCurrentCell(len(rows) - 1, 0)
        self.slots.remove_override()
        self.assertEqual(len(self._table()), 2)


class TestTransferMode(_PanelCase):
    def setUp(self):
        super().setUp()
        self._mode("transfer")

    def test_auto_leads_both_mode_combos(self):
        for combo, data in (
            (self.ui.cmb_tt_transfer, ["auto", "textures", "uvs"]),
            (self.ui.cmb_tt_source, ["auto", "first", "last", "stored", "uvset"]),
        ):
            self.assertEqual([combo.itemData(i) for i in range(combo.count())], data)

    def test_targets_grey_unless_first_selected(self):
        src = self.ui.cmb_tt_source
        for mode, live in (("first", True), ("auto", False), ("last", False)):
            src.setCurrentIndex(src.findData(mode))
            self.sb.QtWidgets.QApplication.processEvents()
            self.assertEqual(self.ui.cmb_tt_targets.isEnabled(), live, mode)

    def test_capture_switches_the_source_to_stored(self):
        cmds.select(cmds.polyCube()[0])
        self.slots.transfer.capture_source()
        self.assertEqual(self.ui.cmb_tt_source.currentData(), "stored")
        self.assertTrue(self.slots.transfer.clear_action.widget.isEnabled())
        self.slots.transfer.clear_source()
        self.assertFalse(self.slots.transfer.clear_action.widget.isEnabled())

    def test_a_blank_name_is_derived_from_the_source_group(self):
        """End to end: a textured source GROUP picked first, its copy second,
        no Output Name -- the maps come out named after the source group."""
        sg = self._material("crate_src", (200, 40, 40))
        grp = cmds.group(empty=True, name="crate_GRP")
        for name in ("lid", "box"):
            cmds.parent(cmds.polyCube(name=name)[0], grp)
            cmds.sets(f"|crate_GRP|{name}", edit=True, forceElement=sg)
        out = cmds.duplicate(grp, name="out_GRP")[0]
        for combo, data in (
            (self.ui.cmb_tt_source, "first"),
            (self.ui.cmb_tt_transfer, "textures"),
            (self.ui.cmb_tt_targets, "order"),
        ):
            combo.setCurrentIndex(combo.findData(data))
        self.ui.cmb_tt_resolution.setCurrentIndex(
            self.ui.cmb_tt_resolution.findData(512)
        )
        cmds.select([grp, out])
        self.slots.b000()
        folder = self.ui.t_output.text()
        self.assertTrue(
            os.path.isfile(os.path.join(folder, "crate_BaseColor.png")), self.boxes
        )
        self.assertIn("Named <b>crate</b> after the source", self.boxes[-1])

    def _overwrite(self):
        """Pick Shader: Overwrite target (restored to the default afterwards)."""
        combo = self.ui.cmb_shader
        self.addCleanup(combo.setCurrentIndex, 0)
        combo.setCurrentIndex(combo.findData("in_place"))
        self.sb.QtWidgets.QApplication.processEvents()

    def test_overwrite_target_is_the_last_shader_item(self):
        """Last, so the combo's index-persisted state still means what it did."""
        combo = self.ui.cmb_shader
        self.assertEqual(combo.itemData(combo.count() - 1), "in_place")
        self.assertEqual(combo.itemData(0), "source")

    def test_overwrite_target_greys_the_affix_and_drops_it(self):
        """The target's own material keeps its name: no affix applies."""
        self.ui.t_affix.setText("_MAT")
        self.addCleanup(self.ui.t_affix.clear)
        self._overwrite()
        self.assertEqual(self.slots.assign_options(), {"assign_from": "in_place"})
        self.assertFalse(self.ui.t_affix.isEnabled())
        self.assertEqual(self.slots.material_affix(), ("", None))
        self.ui.cmb_shader.setCurrentIndex(0)
        self.assertTrue(self.ui.t_affix.isEnabled())

    def test_overwrite_target_rewrites_the_targets_own_map(self):
        """End to end: the target's material and file stay, the file now holds
        the source's texture, and the report names the material kept."""
        src = cmds.polyPlane(name="owSrc", sx=1, sy=1, ch=False)[0]
        cmds.sets(src, e=True, forceElement=self._material("owSrcMat", (220, 30, 30)))
        tgt = cmds.duplicate(src, name="owTgt")[0]
        cmds.sets(tgt, e=True, forceElement=self._material("owTgtMat", (90, 90, 90)))
        own = os.path.join(self.tmp, "owTgtMat.png")
        for combo, data in (
            (self.ui.cmb_tt_source, "first"),
            (self.ui.cmb_tt_transfer, "textures"),
            (self.ui.cmb_tt_targets, "order"),
        ):
            combo.setCurrentIndex(combo.findData(data))
        self.ui.chk_assign.setChecked(True)
        self._overwrite()
        cmds.select([src, tgt], replace=True)
        self.slots.b000()
        self.assertIn(
            "Overwrote <b>1</b> file(s) in place: owTgtMat.png", self.boxes[-1]
        )
        self.assertEqual(TextureTransfer.face_materials(tgt)[0], ["owTgtMat"])
        with Image.open(own) as img:
            red = np.asarray(img.convert("RGB"), np.float32).mean(axis=(0, 1))
        self.assertLess(np.abs(red - (220, 30, 30)).max(), 2.0, self.boxes)

    def test_uv_set_to_uv_set_texture_transfer(self):
        a = cmds.polyPlane(name="tx", sx=1, sy=1, ch=False)[0]
        cmds.sets(a, e=True, forceElement=self._material("matT", (0, 255, 0)))
        cmds.polyUVSet(a, copy=True, uvSet="map1", newUVSet="map2")
        cmds.polyUVSet(a, currentUVSet=True, uvSet="map1")
        src = self.ui.cmb_tt_source
        src.setCurrentIndex(src.findData("uvset"))
        self.assertFalse(self.ui.cmb_tt_transfer.isEnabled())  # pinned to Textures
        self.ui.t_name.setText("moved")
        self.ui.chk_assign.setChecked(False)
        cmds.select(a, replace=True)
        self.slots.b000()
        self.assertTrue(
            self.boxes and "Transferred <b>1</b> map(s)" in self.boxes[-1], self.boxes
        )


if __name__ == "__main__":
    unittest.main()
