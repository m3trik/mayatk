# !/usr/bin/python
# coding=utf-8
"""Tests for the Texture Combine panel's Transfer mode (``_transfer_mode``).

Moved with the code from tentacle's ``test_uv.py`` (the Maya UV panel's b000
Transfer option box, which this mode replaces): the selection gates, Auto's
two picks, Include Lightmaps end to end, the Material row, and Source: Last /
Auto on real geometry. The run reads the panel's widgets, so the doubles below
are the panel's rows by their ``texture_combine.ui`` names; the shared rows'
readers (affix, shader) are the panel's own methods.
"""

import os
import unittest
from unittest import mock

import maya.cmds as cmds
import pythontk as ptk

from base_test import MayaTkTestCase
import mayatk as mtk
from mayatk.uv_utils.texture_combine import _transfer_mode
from mayatk.uv_utils.texture_combine.texture_combine_slots import TextureCombineSlots


class _Progress:
    def __init__(self, log, text):
        self._log, self._text = log, text

    def __enter__(self):
        self._log.append(self._text)
        return self._tick

    def _tick(self, value=None, text=None):
        if text:
            self._log.append(text)
        return True

    def __exit__(self, *exc):
        return False


class _Sb:
    def __init__(self):
        self.messages = []
        self.progress_texts = []

    def message_box(self, text, *args, **kwargs):
        self.messages.append(text)

    def progress(self, ui=None, total=None, text=""):
        return _Progress(self.progress_texts, text)


class _Value:
    """Enough of a combo / field / check / spin for the rows the run reads."""

    def __init__(self, data=None, text="", checked=False, value=-1):
        self._data, self._text, self._checked, self._value = data, text, checked, value

    def currentData(self):
        return self._data

    def text(self):
        return self._text

    def isChecked(self):
        return self._checked

    def value(self):
        return self._value


class _Affix:
    """The Material Affix row: ``resolve_affix`` over the same
    ``ptk.StrUtils.split_affix`` the real ``AffixOption`` uses."""

    def __init__(self, text="", mode="auto"):
        self._text = text
        self.option_box = mock.Mock()
        self.option_box.resolve_affix = lambda text=None, default="prefix": (
            ptk.StrUtils.split_affix(
                self._text if text is None else text, mode=mode, default=default
            )
        )

    def text(self):
        return self._text


def _panel(
    mode="first",
    scope="order",
    transfer="uvs",
    output_name="transfer_result",
    lightmaps=False,
    output_dir="",
    affix="",
    affix_mode="auto",
    shader="source",
):
    slots = TextureCombineSlots.__new__(TextureCombineSlots)
    slots.sb = _Sb()
    slots._name_recent = None
    # The panel in Transfer mode: its mode-scoped readers (overwrites_target).
    slots._fields = mock.Mock(mode=TextureCombineSlots.MODE_TRANSFER)
    ui = mock.Mock()
    ui.cmb_tt_source = _Value(mode)
    ui.cmb_tt_targets = _Value(scope)
    ui.cmb_tt_transfer = _Value(transfer)
    ui.t_name = _Value(text=output_name)
    ui.t_tt_src_uvset = _Value(text="")
    ui.t_tt_dst_uvset = _Value(text="")
    ui.t_output = _Value(text=output_dir)
    ui.cmb_tt_resolution = _Value(0)
    ui.cmb_quality = _Value(2)
    ui.s_padding = _Value(value=-1)
    ui.chk_assign = _Value(checked=True)
    ui.chk_tt_lightmaps = _Value(checked=lightmaps)
    ui.t_affix = _Affix(affix, affix_mode)
    ui.cmb_shader = _Value(shader)
    slots.ui = ui
    slots.transfer = _transfer_mode._TransferMode(slots)
    return slots


class _Case(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.uv_calls = []
        self.space = "topology"

        def fake_transfer_uvs(frm, to, **kwargs):
            self.uv_calls.append((frm, to, kwargs))
            sources = frm if isinstance(frm, (list, tuple)) else [frm]
            targets = to if isinstance(to, (list, tuple)) else [to]
            return [(s, t, self.space) for s, t in zip(sources * len(targets), targets)]

        patcher = mock.patch.object(
            mtk.UvUtils, "transfer_uvs", side_effect=fake_transfer_uvs
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _capture_textures(self, result=None):
        captured = []
        real = _transfer_mode.TextureTransfer

        class Capturing(real):
            def transfer(self, targets, source=None, **kwargs):
                captured.append((list(targets), source, kwargs))
                return result if result is not None else {}

        patcher = mock.patch.object(_transfer_mode, "TextureTransfer", Capturing)
        patcher.start()
        self.addCleanup(patcher.stop)
        return captured

    def _run(self, **kwargs):
        slots = _panel(**kwargs)
        slots.transfer.run()
        return slots

    @staticmethod
    def _leaves(names):
        names = names if isinstance(names, (list, tuple)) else [names]
        return [str(n).rsplit("|", 1)[-1] for n in names]


class TestUvGate(_Case):
    """The UV pass: selection gates, pairing, the footer and the report."""

    def test_no_selection_warns_and_skips(self):
        cmds.select(clear=True)
        slots = self._run()
        self.assertEqual(self.uv_calls, [])
        self.assertTrue(slots.sb.messages)

    def test_one_object_warns_and_skips(self):
        cmds.select(cmds.polyCube(name="tm_one")[0])
        slots = self._run()
        self.assertEqual(self.uv_calls, [])
        self.assertTrue(slots.sb.messages)

    def test_two_objects_transfer_as_one_pair(self):
        a = cmds.polyCube(name="tm_a")[0]
        b = cmds.polyCube(name="tm_b")[0]
        cmds.select([a, b])
        self._run()
        self.assertEqual(len(self.uv_calls), 1)
        sources, targets, kwargs = self.uv_calls[0]
        self.assertEqual(self._leaves(sources), [a])
        self.assertEqual(self._leaves(targets), [b])
        # The selection order IS the correspondence: never re-vetted.
        self.assertIs(kwargs.get("match_by_similarity"), False)

    def test_the_footer_names_each_pass(self):
        a = cmds.polyCube(name="tm_p1")[0]
        b = cmds.polyCube(name="tm_p2")[0]
        cmds.select([a, b])
        self.assertTrue(
            any("Transfer UV Set" in t for t in self._run().sb.progress_texts)
        )
        self._capture_textures()
        self.assertIn(
            "Working: Transfer Textures",
            self._run(transfer="textures").sb.progress_texts,
        )

    def test_three_objects_pair_the_source_against_every_target(self):
        a, b, c = (cmds.polyCube(name=f"tm_{n}3")[0] for n in "abc")
        cmds.select([a, b, c])
        self._run()
        sources, targets, _ = self.uv_calls[0]
        self.assertEqual(set(self._leaves(sources)), {a})
        self.assertEqual(set(self._leaves(targets)), {b, c})

    def test_exact_transfer_stays_quiet_and_approximation_is_reported(self):
        a, b, c = (cmds.polyCube(name=f"tm_q{n}")[0] for n in "abc")
        cmds.select([a, b])
        self.assertNotIn("proximity", "".join(self._run().sb.messages))
        self.space = "object"
        cmds.select([a, b, c])
        message = self._run().sb.messages[0]
        self.assertIn("2", message)
        self.assertIn("proximity", message.lower())


class TestAuto(_Case):
    """Transfer: Auto picks the pass from the SOURCE's materials."""

    def test_a_mapped_source_resolves_to_the_texture_pass(self):
        src = cmds.polyCube(name="tm_auto_src")[0]
        tgt = cmds.polyCube(name="tm_auto_tgt")[0]
        mat = cmds.shadingNode("lambert", asShader=True, name="tm_auto_mat")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="tm_auto_sg"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader")
        f = cmds.shadingNode("file", asTexture=True)
        cmds.setAttr(f"{f}.fileTextureName", "sourceimages/tm_auto.png", type="string")
        cmds.connectAttr(f"{f}.outColor", f"{mat}.color")
        cmds.sets(src, edit=True, forceElement=sg)
        cmds.select([src, tgt])
        calls = self._capture_textures()
        slots = self._run(transfer="auto", output_name="")
        self.assertEqual(self.uv_calls, [])
        self.assertEqual(len(calls), 1, slots.sb.messages)
        self.assertEqual(calls[0][2]["output_name"], "tm_auto_src")

    def test_an_unmapped_source_resolves_to_the_uv_pass(self):
        a = cmds.polyCube(name="tm_plain_a")[0]
        b = cmds.polyCube(name="tm_plain_b")[0]
        cmds.select([a, b])
        slots = self._run(transfer="auto", output_name="")
        self.assertEqual(len(self.uv_calls), 1)
        self.assertIn("Working: Transfer UV Set", slots.sb.progress_texts)


class TestLightmaps(_Case):
    """Include Lightmaps end to end through the REAL engine."""

    def setUp(self):
        super().setUp()
        self._artifacts = ptk.TempArtifacts("tm_lightmaps", policy="scoped")
        self.addCleanup(self._artifacts.cleanup)
        self.tmp = self._artifacts.dir_path()

    def _lit_source_and_target(self):
        import numpy as np

        src = cmds.polyPlane(name="tm_lm_src", sx=2, sy=2, ch=False)[0]
        cmds.polyUVSet(src, copy=True, uvSet="map1", newUVSet="lightmap")
        path = os.path.join(self.tmp, "tm_lm_src_Lightmap.exr").replace("\\", "/")
        mtk.LightmapRecords._write_lightmap(path, np.full((16, 16, 3), 3.0, np.float32))
        mtk.LightmapRecords.commit({src: path})
        tgt = cmds.duplicate(src, name="tm_lm_tgt")[0]
        cmds.deleteAttr(f"{tgt}.lightmapInfo")
        cmds.polyUVSet(tgt, currentUVSet=True, uvSet="lightmap")
        cmds.polyEditUV(
            f"{tgt}.map[*]",
            uvSetName="lightmap",
            rotation=True,
            angle=90,
            pivotU=0.5,
            pivotV=0.5,
        )
        cmds.polyUVSet(tgt, currentUVSet=True, uvSet="map1")
        return src, tgt

    def test_auto_carries_a_lit_untextured_source_s_lightmap(self):
        src, tgt = self._lit_source_and_target()
        cmds.select([src, tgt])
        out_dir = os.path.join(self.tmp, "out").replace("\\", "/")
        slots = self._run(
            transfer="auto", output_name="hero", lightmaps=True, output_dir=out_dir
        )
        message = "".join(slots.sb.messages)
        self.assertIn("resampled into the target's layout", message)
        self.assertEqual(
            mtk.LightmapRecords.lightmap_info(tgt).get("map"), "hero_Lightmap.exr"
        )
        self.assertTrue(os.path.isfile(os.path.join(out_dir, "hero_Lightmap.exr")))

    def test_unchecked_auto_moves_the_layout_and_no_lightmap(self):
        src, tgt = self._lit_source_and_target()
        cmds.select([src, tgt])
        slots = self._run(transfer="auto", output_name="", lightmaps=False)
        self.assertIn("Working: Transfer UV Set", slots.sb.progress_texts)
        self.assertEqual(mtk.LightmapRecords.lightmap_info(tgt), {})


class TestMaterialRow(_Case):
    """The Material Affix and Shader rows reach the engine as plain keywords."""

    def _kwargs(self, **kwargs):
        calls = self._capture_textures()
        a = cmds.polyCube(name="tm_affix_a")[0]
        b = cmds.polyCube(name="tm_affix_b")[0]
        cmds.select([a, b])
        slots = self._run(transfer="textures", **kwargs)
        self.assertEqual(len(calls), 1, slots.sb.messages)
        return calls[0][2]

    def test_blank_affix_leaves_the_naming_to_the_engine(self):
        kwargs = self._kwargs()
        self.assertEqual(kwargs["assign_prefix"], "")
        self.assertIsNone(kwargs["assign_suffix"])

    def test_auto_reads_the_underscore(self):
        self.assertEqual(self._kwargs(affix="_MAT")["assign_suffix"], "_MAT")
        self.assertEqual(self._kwargs(affix="MAT_")["assign_prefix"], "MAT_")

    def test_the_picker_pins_an_undeclared_side(self):
        kwargs = self._kwargs(affix="MAT", affix_mode="prefix")
        self.assertEqual(
            (kwargs["assign_prefix"], kwargs["assign_suffix"]), ("MAT", "")
        )
        kwargs = self._kwargs(affix="MAT", affix_mode="suffix")
        self.assertEqual(
            (kwargs["assign_prefix"], kwargs["assign_suffix"]), ("", "MAT")
        )

    def test_auto_falls_back_to_suffix(self):
        kwargs = self._kwargs(affix="MAT")
        self.assertEqual(
            (kwargs["assign_prefix"], kwargs["assign_suffix"]), ("", "MAT")
        )

    def test_shader_rows(self):
        kwargs = self._kwargs(shader="stingray")
        self.assertEqual(
            (kwargs["assign_shader_type"], kwargs["assign_from"]),
            ("stingray", "source"),
        )
        kwargs = self._kwargs(shader="target")
        self.assertEqual(kwargs["assign_from"], "target")
        self.assertNotIn("assign_shader_type", kwargs)
        self.assertEqual(self._kwargs()["assign_from"], "source")

    def test_overwrite_target_re_uses_the_material_and_drops_the_affix(self):
        """Overwrite target keeps the target's material as it is named."""
        kwargs = self._kwargs(shader="in_place", affix="_MAT")
        self.assertEqual(kwargs["assign_from"], "in_place")
        self.assertNotIn("assign_shader_type", kwargs)
        self.assertEqual((kwargs["assign_prefix"], kwargs["assign_suffix"]), ("", None))

    def test_a_blank_output_name_is_named_after_the_source(self):
        calls = self._capture_textures()
        grp = cmds.group(empty=True, name="hero_GRP")
        src = cmds.parent(cmds.polyCube(name="hero_body_GEO")[0], grp)[0]
        tgt = cmds.polyCube(name="tm_named_tgt")[0]
        cmds.select([cmds.ls(src, long=True)[0], tgt])
        self._run(transfer="textures", output_name="")
        self.assertEqual(calls[0][2]["output_name"], "hero_body")


class TestSourceLastAndAuto(_Case):
    """Source: All But Last Selected, and Source: Auto, on real geometry."""

    def setUp(self):
        super().setUp()
        self.calls = self._capture_textures(
            {"m": {"baseColor": "/out/hero_BaseColor.png"}}
        )

    def _scene(self):
        a = cmds.polyCube(name="srcA")[0]
        b = cmds.polySphere(name="srcB", sx=6, sy=4)[0]
        cmds.move(3, 0, 0, b)
        c = cmds.polyUnite(cmds.duplicate([b, a]), ch=False, name="comb")[0]
        c = cmds.rename(cmds.parent(c, cmds.group(empty=True, name="out"))[0], a)
        return (cmds.ls(n, long=True)[0] for n in (f"|{a}", f"|{b}", c))

    def _pairs(self):
        return [(t, s) for t, s, _ in self.calls]

    def test_last_selected_is_the_target_of_every_other_mesh(self):
        a, b, c = self._scene()
        cmds.select([a, b, c])
        self._run(mode="last", transfer="textures", output_name="hero")
        self.assertEqual(self._pairs(), [([c], [a, b])])

    def test_last_refuses_the_uv_pass_onto_a_combined_target(self):
        a, b, c = self._scene()
        cmds.select([a, b, c])
        slots = self._run(mode="last", transfer="uvs", output_name="hero")
        self.assertEqual(self.uv_calls, [])
        self.assertIn("one source per target", slots.sb.messages[-1])

    def test_auto_finds_the_combined_target_wherever_it_was_picked(self):
        a, b, c = self._scene()
        cmds.select([c, a, b])
        slots = self._run(mode="auto", transfer="textures", output_name="hero")
        self.assertEqual(self._pairs(), [([c], [a, b])])
        self.assertIn("Source: Auto", slots.sb.messages[-1])

    def test_auto_reads_one_mesh_as_its_own_uv_sets(self):
        a, _, _ = self._scene()
        cmds.select(a)
        self._run(mode="auto", transfer="textures", output_name="hero")
        self.assertEqual(self._pairs(), [([a], None)])

    def test_auto_falls_back_to_first_selected(self):
        a, b, _ = self._scene()
        cmds.select([b, a])
        self._run(mode="auto", transfer="textures", output_name="hero")
        self.assertEqual(self._pairs(), [([a], [b])])

    def test_auto_pairs_a_source_group_with_a_target_group(self):
        src = cmds.group(empty=True, name="kit_ORIG")
        tgt = cmds.group(empty=True, name="kit_GRP")
        for n in ("partA", "partB"):
            cmds.parent(cmds.polyCube(name=n)[0], src)
            dup = cmds.duplicate(f"|kit_ORIG|{n}")[0]
            moved = cmds.parent(f"|kit_ORIG|{dup}", tgt)[0]
            cmds.rename(f"|kit_GRP|{moved}", n)
        cmds.select([src, tgt])
        self._run(mode="auto", transfer="textures", output_name="hero")
        ((targets, source),) = self._pairs()
        self.assertEqual(
            targets, cmds.ls("|kit_GRP|partA", "|kit_GRP|partB", long=True)
        )
        self.assertEqual(
            source, cmds.ls("|kit_ORIG|partA", "|kit_ORIG|partB", long=True)
        )

    def test_auto_reads_stored_sources_that_match_the_selection(self):
        a, b, c = self._scene()
        slots = _panel(mode="auto", transfer="textures", output_name="hero")
        slots.transfer.sources = [a, b]
        cmds.select(c)
        slots.transfer.run()
        self.assertEqual(self._pairs(), [([c], [a, b])])
        self.assertIn("stored source meshes", slots.sb.messages[-1])

    def test_auto_ignores_a_stale_capture(self):
        a, _, _ = self._scene()
        slots = _panel(mode="auto", transfer="textures", output_name="hero")
        slots.transfer.sources = [cmds.ls(cmds.polyTorus()[0], long=True)[0]]
        cmds.select(a)
        slots.transfer.run()
        self.assertEqual(self._pairs(), [([a], None)])

    def test_first_selected_feeds_every_target(self):
        a = cmds.ls(cmds.polyCube(name="feedSrc")[0], long=True)[0]
        t1 = cmds.ls(cmds.duplicate(a, name="feedT1")[0], long=True)[0]
        t2 = cmds.ls(cmds.duplicate(a, name="feedT2")[0], long=True)[0]
        cmds.select([a, t1, t2])
        self._run(mode="first", transfer="textures", output_name="hero")
        self.assertEqual(self._pairs(), [([t1, t2], [a])])


class TestUndo(_Case):
    """One run is ONE undo step, as tentacle's b000 (``@mtk.undoable``) was."""

    def setUp(self):
        super().setUp()
        self._artifacts = ptk.TempArtifacts("tm_undo", policy="scoped")
        self.addCleanup(self._artifacts.cleanup)
        self.tmp = self._artifacts.dir_path()

    def test_one_undo_takes_the_texture_pass_back(self):
        import numpy as np
        from PIL import Image

        texture = os.path.join(self.tmp, "tm_undo.png").replace("\\", "/")
        Image.fromarray(np.full((8, 8, 3), (255, 0, 0), np.uint8)).save(texture)
        src = cmds.polyPlane(name="tm_undo_src", sx=1, sy=1, ch=False)[0]
        mat = cmds.shadingNode("lambert", asShader=True, name="tm_undo_mat")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="tm_undo_sg"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader")
        f = cmds.shadingNode("file", asTexture=True)
        cmds.setAttr(f"{f}.fileTextureName", texture, type="string")
        cmds.connectAttr(f"{f}.outColor", f"{mat}.color")
        cmds.sets(src, edit=True, forceElement=sg)
        tgt = cmds.duplicate(src, name="tm_undo_tgt")[0]
        cmds.polyEditUV(
            f"{tgt}.map[*]", rotation=True, angle=90, pivotU=0.5, pivotV=0.5
        )
        cmds.select([src, tgt])
        out_dir = os.path.join(self.tmp, "out").replace("\\", "/")
        slots = self._run(
            transfer="textures", output_name="tm_undo_hero", output_dir=out_dir
        )
        worn = mtk.TextureTransfer.face_materials(tgt)[0]
        self.assertNotEqual(worn, ["tm_undo_mat"], slots.sb.messages)
        cmds.undo()
        self.assertEqual(mtk.TextureTransfer.face_materials(tgt)[0], ["tm_undo_mat"])


class TestStoredSources(_Case):
    """The Source combo's stored set -- invisible state, so its icons, its
    Select verb and its live tooltip are how it is inspected."""

    def _panel(self):
        slots = _panel(mode="stored")
        slots.status = lambda text: slots.sb.messages.append(text)
        slots.sb.tooltip = ptk.TooltipFormat
        slots.transfer.select_action = mock.Mock()
        slots.transfer.clear_action = mock.Mock()
        slots.transfer.sync = lambda: None
        return slots

    def test_capture_stores_the_selection_and_switches_to_stored(self):
        slots = self._panel()
        slots.ui.cmb_tt_source = mock.Mock()
        slots.ui.cmb_tt_source.findData.return_value = 3
        cmds.select(cmds.polyCube()[0], replace=True)
        slots.transfer.capture_source()
        self.assertEqual(len(slots.transfer.sources), 1)
        slots.ui.cmb_tt_source.setCurrentIndex.assert_called_once_with(3)

    def test_select_selects_the_stored_meshes(self):
        slots = self._panel()
        a, b = cmds.polyCube()[0], cmds.polyCube()[0]
        slots.transfer.sources = cmds.ls([a, b], long=True)
        cmds.select(clear=True)
        slots.transfer.select_source()
        self.assertEqual(
            sorted(cmds.ls(selection=True, long=True)), sorted(slots.transfer.sources)
        )

    def test_select_skips_nodes_deleted_since_the_capture(self):
        slots = self._panel()
        cube = cmds.polyCube()[0]
        slots.transfer.sources = cmds.ls(cube, long=True) + ["|goneForever"]
        cmds.select(clear=True)
        slots.transfer.select_source()
        self.assertEqual(cmds.ls(selection=True, long=True), cmds.ls(cube, long=True))
        self.assertIn("1 no longer in the scene", slots.sb.messages[-1])

    def test_select_with_nothing_stored_leaves_the_selection_alone(self):
        slots = self._panel()
        cube = cmds.polyCube()[0]
        cmds.select(cube, replace=True)
        slots.transfer.select_source()
        self.assertEqual(cmds.ls(selection=True, long=True), cmds.ls(cube, long=True))
        self.assertIn("Nothing stored", slots.sb.messages[-1])

    def test_tooltip_lists_short_names_not_full_dag_paths(self):
        slots = self._panel()
        slots.transfer.sources = ["|grp|pCube1", "|grp|pCube2"]
        html = slots.transfer.source_tooltip()
        self.assertIn("<li>pCube1</li>", html)
        self.assertNotIn("grp", html)

    def test_tooltip_truncates_a_long_capture(self):
        slots = self._panel()
        n = ptk.TooltipFormat.STORED_ITEMS_MAX
        slots.transfer.sources = [f"|pCube{i}" for i in range(n + 4)]
        html = slots.transfer.source_tooltip()
        self.assertEqual(html.count("<li>"), n)
        self.assertIn("4 more", html)

    def test_tooltip_empty_still_explains_the_button(self):
        slots = self._panel()
        html = slots.transfer.source_tooltip()
        self.assertNotIn("<li>", html)
        self.assertIn("Store Source Meshes", html)

    def test_tooltip_calls_out_deleted_nodes(self):
        slots = self._panel()
        cube = cmds.polyCube()[0]
        slots.transfer.sources = cmds.ls(cube, long=True) + ["|goneForever"]
        self.assertIn("1 no longer in the scene", slots.transfer.source_tooltip())


if __name__ == "__main__":
    unittest.main()
