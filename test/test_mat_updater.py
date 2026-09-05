# !/usr/bin/python
# coding=utf-8
"""Test Suite for mat_utils.mat_updater.

The texture pipeline itself is pinned upstream (pythontk's MapFactory) and the
wiring by ``test_game_shader``; what is this class's own is *which materials a
run acts on* -- and, since the Shader Type option, the retype it runs before
wiring them. A legacy blinn has no connector, so it used to be dropped by the
run that most needed it.
"""

import os
import unittest

import maya.cmds as cmds
import pythontk as ptk

from mayatk.mat_utils.mat_updater import MatUpdater, MatUpdaterSlots
from mayatk.mat_utils.game_shader import GameShader
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.mat_utils.shader_converter import ShaderConverter

from base_test import MayaTkTestCase


class _MaterialSceneMixin:
    """A textured blinn on a plane -- the legacy material the retype targets."""

    def build_blinn(self, name="legacy_blinn"):
        self.artifacts = ptk.TempArtifacts("mtk_mat_updater", policy="scoped")
        self.base_map = self._png(self.artifacts.dir_path(), "WALL_Base_Color")

        mat = cmds.shadingNode("blinn", asShader=True, name=name)
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        node = cmds.shadingNode("file", asTexture=True, name=f"{name}_color")
        cmds.setAttr(f"{node}.fileTextureName", self.base_map, type="string")
        cmds.connectAttr(f"{node}.outColor", f"{mat}.color", force=True)

        self.plane = cmds.polyPlane(name="WALL", constructionHistory=False)[0]
        cmds.sets(self.plane, edit=True, forceElement=sg)
        return mat

    @staticmethod
    def _png(directory, stem):
        """A real 8x8 RGBA PNG, named so the map-type resolver classifies it."""
        import os
        import struct
        import zlib

        path = os.path.join(directory, f"{stem}.png").replace("\\", "/")

        def chunk(tag, data):
            return (
                struct.pack(">I", len(data))
                + tag
                + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
            )

        raw = (b"\x00" + bytes((128, 128, 128, 255)) * 8) * 8
        ihdr = struct.pack(">IIBBBBB", 8, 8, 8, 6, 0, 0, 0)
        with open(path, "wb") as fh:
            fh.write(
                b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", ihdr)
                + chunk(b"IDAT", zlib.compress(raw))
                + chunk(b"IEND", b"")
            )
        return path


class TestRetypeMaterials(MayaTkTestCase, _MaterialSceneMixin):
    """``MatUpdater._retype_materials`` -- the pick, not the conversion.

    The conversion is ``ShaderConverter``'s and is pinned by
    ``test_shader_converter``; what this owns is which materials go into it and
    what the caller gets back to carry forward.
    """

    def setUp(self):
        super().setUp()
        self.mat = self.build_blinn()

    def tearDown(self):
        self.artifacts.cleanup()
        super().tearDown()

    def test_a_converted_material_is_replaced_in_the_list(self):
        # By UUID, not by name: the converter RECLAIMS the source's name, so
        # the entry that comes back reads identical while naming a new node.
        was = cmds.ls(self.mat, uuid=True)[0]
        out = MatUpdater._retype_materials([self.mat], "standard_surface")
        self.assertEqual(len(out), 1)
        self.assertEqual(cmds.nodeType(out[0]), "standardSurface")
        self.assertNotEqual(cmds.ls(out[0], uuid=True)[0], was)
        self.assertFalse(cmds.ls(was, uuid=True), "the blinn should be retired")
        # And the geometry came with it -- a retyped material nothing wears
        # would be a silent loss of the assignment. Read through
        # get_shading_assignments: a shading engine connects to the SHAPE, so
        # querying the transform finds nothing whether or not the move worked.
        from mayatk.mat_utils._mat_utils import MatUtils

        shaders = [
            (cmds.listConnections(f"{sg}.surfaceShader") or [None])[0]
            for sg in MatUtils.get_shading_assignments(self.plane)
        ]
        self.assertIn(out[0], shaders)

    def test_a_material_already_of_the_target_type_is_left_alone(self):
        already = cmds.shadingNode("standardSurface", asShader=True, name="pbr_mat")
        out = MatUpdater._retype_materials([already], "standard_surface")
        self.assertEqual(out, [already])
        self.assertTrue(cmds.objExists(already))

    def test_dry_run_retypes_nothing(self):
        out = MatUpdater._retype_materials([self.mat], "stingray", dry_run=True)
        self.assertEqual(out, [self.mat])
        self.assertEqual(cmds.nodeType(self.mat), "blinn")

    def test_an_unknown_target_is_refused_before_anything_is_touched(self):
        """Including on a dry run, which never reaches the conversion's own
        validation -- so it would otherwise report a plan that cannot run."""
        for dry in (False, True):
            with self.assertRaises(ValueError):
                MatUpdater._retype_materials([self.mat], "not_a_shader", dry_run=dry)
        self.assertEqual(cmds.nodeType(self.mat), "blinn")

    def test_an_unconvertible_material_keeps_its_place(self):
        """``convert`` reports None for a type it cannot read; the caller must
        carry the original forward rather than a hole in the list."""
        surface = cmds.shadingNode("surfaceShader", asShader=True, name="raw_surface")
        out = MatUpdater._retype_materials([surface], "standard_surface")
        self.assertEqual(out, [surface])
        self.assertTrue(cmds.objExists(surface))


class TestUpdateMaterialsRetype(MayaTkTestCase, _MaterialSceneMixin):
    """The integration claim: a retype run WIRES the material it converted.

    Without the retype the blinn is dropped by the connector filter -- so this
    pins the ordering (retype first, filter second), not just the conversion.
    """

    def setUp(self):
        super().setUp()
        self.mat = self.build_blinn()
        # Reconfiguration only: the image factory is off, so the run wires the
        # textures already on the material instead of processing files. Keeps
        # the test about the routing, and off pythontk's pipeline.
        self.config = {
            "convert": False,
            "optimize": False,
            "convert_format": False,
            "convert_type": False,
            "resize": False,
            "pack": False,
        }

    def tearDown(self):
        self.artifacts.cleanup()
        super().tearDown()

    def test_a_legacy_material_is_retyped_then_updated(self):
        was = cmds.ls(self.mat, uuid=True)[0]
        results = MatUpdater.update_materials(
            materials=[self.mat],
            config=dict(self.config),
            shader_type="standard_surface",
        )
        # The blinn NODE is gone; its name lives on, reclaimed by the shader
        # that replaced it (ShaderConverter._claim_name), which is why this
        # checks the uuid rather than objExists.
        self.assertFalse(cmds.ls(was, uuid=True), "the blinn should be retired")
        updated = list(results)
        self.assertEqual(len(updated), 1, results)
        self.assertEqual(cmds.nodeType(updated[0]), "standardSurface")
        self.assertTrue(
            cmds.listConnections(f"{updated[0]}.baseColor", type="file"),
            "the retyped material reached the wiring stage",
        )

    def test_without_a_shader_type_the_legacy_material_is_skipped(self):
        """The pre-existing behaviour, pinned so the widening above is visibly
        the thing that changes it."""
        results = MatUpdater.update_materials(
            materials=[self.mat], config=dict(self.config)
        )
        self.assertEqual(results, {})
        self.assertEqual(cmds.nodeType(self.mat), "blinn")


class TestUpdateNetworkKeepsOpacity(MayaTkTestCase):
    """``update_network`` has to settle the opacity the way a build does.

    StingrayPBS has no sampler for a SEPARATE opacity map -- the alpha rides
    the colour map (`GameShader.OPACITY_SLOTS`). A build packs the two before
    wiring; the rewire did not, so a set the factory had split into a
    `Base_Color` + an `Opacity` (what every preset with ``albedo_transparency``
    off produces) disconnected the material's working alpha and reported the
    replacement as "no slot for Opacity; skipped". The transparency was gone,
    and the run reported success.
    """

    def setUp(self):
        super().setUp()
        from PIL import Image

        self.artifacts = ptk.TempArtifacts("mtk_updater_opacity", policy="scoped")
        directory = self.artifacts.dir_path()

        self.base_map = os.path.join(directory, "DECAL_Base_Color.png")
        Image.new("RGB", (16, 16), (200, 40, 40)).save(self.base_map)
        self.opacity_map = os.path.join(directory, "DECAL_Opacity.png")
        alpha = Image.new("L", (16, 16), 255)
        alpha.putpixel((0, 0), 40)
        alpha.save(self.opacity_map)

        # The material the user's run hit: the transparent graph, already
        # wearing an alpha, being re-pointed at a freshly processed set.
        self.mat = MatUtils.create_stingray_shader(
            "DECAL_MAT", opacity_mode="transparent"
        )
        self.assertEqual(
            MatUtils.get_stingray_opacity_mode(self.mat),
            "transparent",
            "premise: the graph under test is the one with no opacity sampler",
        )

    def test_a_standalone_opacity_map_reaches_the_colour_map_alpha(self):
        connected = MatUpdater.update_network(
            self.mat, [self.base_map, self.opacity_map], {}
        )

        self.assertNotIn(
            "Opacity",
            connected,
            "a separate Opacity map has no slot here -- it must be folded in, "
            "not reported as connected",
        )
        self.assertIn(
            "Albedo_Transparency",
            connected,
            "the Base_Color and the Opacity must arrive as one packed map",
        )
        self.assertEqual(
            cmds.getAttr(f"{self.mat}.use_opacity_map"),
            1.0,
            "the selector must point the graph at the colour map's alpha",
        )
        wired = cmds.listConnections(
            f"{self.mat}.TEX_color_map", source=True, destination=False
        )
        self.assertTrue(wired, "a colour map must be wired")
        packed = cmds.getAttr(f"{wired[0]}.fileTextureName")
        self.assertTrue(
            GameShader()._carries_alpha(packed),
            f"the wired colour map must carry the opacity in its alpha: {packed}",
        )

    def test_a_uniformly_opaque_opacity_map_is_retired_not_wired(self):
        """The other half of the shared resolution: an inert source.

        Painter's default templates ship a solid-white ``_Opacity`` beside
        every opaque set. Packing it would rewrite the colour map and put the
        meshes through the transparent queue for nothing.
        """
        from PIL import Image

        inert = os.path.join(self.artifacts.dir_path(), "SOLID_Opacity.png")
        Image.new("L", (16, 16), 255).save(inert)
        base = os.path.join(self.artifacts.dir_path(), "SOLID_Base_Color.png")
        Image.new("RGB", (16, 16), (40, 90, 200)).save(base)

        connected = MatUpdater.update_network(self.mat, [base, inert], {})

        self.assertNotIn("Opacity", connected)
        self.assertNotIn(
            "Albedo_Transparency",
            connected,
            "nothing to make transparent -- the colour map must not be rewritten",
        )
        self.assertEqual(connected.get("Base_Color"), base)


class _FakeCombo:
    def __init__(self, data):
        self._data = data

    def currentData(self):
        return self._data


class _FakeUi:
    pass


class TestPanelAcceptsWhatItCanRetype(MayaTkTestCase):
    """``MatUpdaterSlots`` filters the selection BEFORE the engine sees it.

    So the panel's own notion of "supported" has to widen with the Shader Type
    or the option is unreachable for every material it exists for.
    """

    def _slots(self, shader_type):
        instance = MatUpdaterSlots.__new__(MatUpdaterSlots)
        ui = _FakeUi()
        ui.header = _FakeUi()
        ui.header.menu = _FakeUi()
        ui.header.menu.cmb_shader_type = _FakeCombo(shader_type)
        instance.ui = ui
        return instance

    def test_keep_current_type_accepts_only_the_wireable_types(self):
        instance = self._slots(None)
        self.assertEqual(
            instance.acceptable_types, tuple(sorted(MatUpdater.SUPPORTED_MAT_TYPES))
        )
        blinn = cmds.shadingNode("blinn", asShader=True)
        self.assertEqual(instance._filter_supported([blinn]), [])

    def test_a_retype_target_accepts_what_the_converter_can_read(self):
        instance = self._slots("stingray")
        self.assertIn("blinn", instance.acceptable_types)
        for node_type in ShaderConverter.CONVERTIBLE:
            self.assertIn(node_type, instance.acceptable_types)
        blinn = cmds.shadingNode("blinn", asShader=True)
        self.assertEqual(instance._filter_supported([blinn]), [blinn])


class _FakeButton:
    """Enough QPushButton for the footer's Apply affordance."""

    def __init__(self):
        self._visible = True
        self.callback = None

    def setVisible(self, state):
        self._visible = bool(state)

    def isVisible(self):
        return self._visible

    def click(self):
        if self.callback is not None:
            self.callback()


class _FakeFooter:
    def __init__(self):
        self.button = None
        self.status = None

    def add_action_button(self, text="", icon_name=None, tooltip="", callback=None):
        self.button = _FakeButton()
        self.button.callback = callback
        return self.button

    def setText(self, text, level=None):
        self.status = (text, level)


class _FakeCheck:
    def __init__(self, state):
        self._state = bool(state)

    def isChecked(self):
        return self._state


class _FakeTextCombo(_FakeCombo):
    """A combo the panel reads by text (preset, selection mode)."""

    def __init__(self, text, data=None):
        super().__init__(data)
        self._text = text

    def currentText(self):
        return self._text

    def setCurrentText(self, text):
        self._text = text


class _FakeLineEdit:
    def __init__(self, text=""):
        self._text = text

    def text(self):
        return self._text


class _FakeOutput:
    def __init__(self):
        self.cleared = 0

    def clear(self):
        self.cleared += 1


class _FakeProgress:
    def __enter__(self):
        return lambda *args, **kwargs: True

    def __exit__(self, *exc):
        return False


class _FakeSwitchboard:
    def progress(self, **kwargs):
        return _FakeProgress()

    def progress_adapter(self, update):
        return None


class TestDryRunArmsApply(MayaTkTestCase):
    """The footer's Apply button: what arms it, and what it commits.

    The value the button carries is that the applied run is the *preview's own
    call* with ``dry_run`` off -- same materials, same options -- so what the
    report on screen describes is what gets written. These pin that, plus the
    three ways a plan must go stale: a failed preview, a new run, and a change
    of selection mode.
    """

    def _slots(self, mode="All Scene Materials", dry_run=True, raises=False):
        instance = MatUpdaterSlots.__new__(MatUpdaterSlots)
        instance.sb = _FakeSwitchboard()
        instance._pending = None
        instance._committing = None

        ui = _FakeUi()
        ui.txt001 = _FakeOutput()
        ui.cmb001 = _FakeTextCombo("PBR Metallic/Roughness")
        ui.cmb_selection_mode = _FakeTextCombo(mode)
        ui.txt_move_to = _FakeLineEdit("")
        ui.footer = _FakeFooter()
        ui.b001 = _FakeButton()
        ui.b001.callback = lambda: instance.b001(None)

        menu = _FakeUi()
        menu.chk_dry_run = _FakeCheck(dry_run)
        menu.cmb_shader_type = _FakeCombo(None)
        menu.cmb_transfer_mode = _FakeCombo("none")
        menu.cmb_missing_maps = _FakeCombo(ptk.MapRegistry.MISSING_SKIP)
        menu.chk_input_fallbacks = _FakeCheck(True)
        menu.chk_output_fallbacks = _FakeCheck(True)
        menu.chk_discover_sourceimages = _FakeCheck(False)
        ui.header = _FakeUi()
        ui.header.menu = menu

        instance.ui = ui
        instance._apply_btn = instance._add_apply_button()

        instance.calls = []

        def _update(**kwargs):
            instance.calls.append(kwargs)
            if raises:
                raise RuntimeError("boom")
            return {}

        instance.update_materials = _update
        return instance

    @staticmethod
    def _assigned_standard_surface(name):
        """A standardSurface on its own plane -- a material the panel accepts."""
        mat = cmds.shadingNode("standardSurface", asShader=True, name=name)
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        plane = cmds.polyPlane(name=f"{name}_geo", constructionHistory=False)[0]
        cmds.sets(plane, edit=True, forceElement=sg)
        return mat, plane

    def test_a_dry_run_arms_the_footer_button(self):
        slots = self._slots(dry_run=True)
        slots.b001(None)

        self.assertEqual(len(slots.calls), 1)
        self.assertIs(slots.calls[0]["config"]["dry_run"], True)
        self.assertTrue(slots._apply_btn.isVisible())
        self.assertIsNotNone(slots._pending)
        self.assertEqual(slots.ui.footer.status[1], "warning")

    def test_a_live_run_never_arms(self):
        slots = self._slots(dry_run=False)
        slots.b001(None)

        self.assertEqual(len(slots.calls), 1)
        self.assertIs(slots.calls[0]["config"]["dry_run"], False)
        self.assertFalse(slots._apply_btn.isVisible())
        self.assertIsNone(slots._pending)

    def test_apply_commits_the_previewed_call_with_dry_run_off(self):
        slots = self._slots(dry_run=True)
        slots.b001(None)
        slots._apply_dry_run()

        self.assertEqual(len(slots.calls), 2)
        preview, commit = slots.calls
        self.assertIs(preview["config"]["dry_run"], True)
        self.assertIs(commit["config"]["dry_run"], False)
        # Everything BUT dry_run is the preview's, so the commit is the plan
        # that was reported rather than a fresh read of the panel.
        self.assertEqual(
            {k: v for k, v in preview["config"].items() if k != "dry_run"},
            {k: v for k, v in commit["config"].items() if k != "dry_run"},
        )
        self.assertEqual(preview["materials"], commit["materials"])
        # And it disarms: the plan has been spent.
        self.assertFalse(slots._apply_btn.isVisible())
        self.assertIsNone(slots._pending)
        self.assertIsNone(slots._committing)
        self.assertEqual(slots.ui.footer.status, ("", None))

    def test_a_press_during_the_commit_starts_a_new_run(self):
        """The commit pumps the event loop, so a second press re-enters b001.

        ``_apply_dry_run`` clears the carrier in a ``finally`` that has not run
        while the commit is in flight, so the carrier has to be consumed where
        ``b001`` READS it -- otherwise the re-entrant press replays the plan
        that is already running instead of starting a run of its own.
        """
        slots = self._slots(dry_run=True)
        slots.b001(None)  # arm

        reentered = []

        def _reenter(**kwargs):
            slots.calls.append(kwargs)
            if len(slots.calls) == 2:  # we are inside the commit
                reentered.append(True)
                slots.b001(None)
            return {}

        slots.update_materials = _reenter
        slots._apply_dry_run()

        self.assertTrue(reentered, "the probe never re-entered b001")
        self.assertEqual(len(slots.calls), 3, "expected preview, commit, new run")
        self.assertIs(slots.calls[1]["config"]["dry_run"], False, "the commit")
        # Dry Run is still ticked, so a FRESH read of the panel previews again.
        # A replay of the committed plan would read False here.
        self.assertIs(slots.calls[2]["config"]["dry_run"], True, "a new preview")

    def test_apply_commits_the_selection_as_it_was_previewed(self):
        """The frozen list, not a re-read -- the selection may have moved on."""
        first, first_geo = self._assigned_standard_surface("previewed_mat")
        second, second_geo = self._assigned_standard_surface("other_mat")

        slots = self._slots(mode="Selected Objects", dry_run=True)
        cmds.select(first_geo, replace=True)
        slots.b001(None)
        previewed = list(slots.calls[0]["materials"])
        self.assertEqual(previewed, [first])

        cmds.select(second_geo, replace=True)  # the artist moves on
        slots._apply_dry_run()

        self.assertEqual(slots.calls[1]["materials"], previewed)
        self.assertNotIn(second, slots.calls[1]["materials"])

    def test_a_failed_preview_leaves_the_button_disarmed(self):
        """A run that raised must not offer to commit itself."""
        slots = self._slots(dry_run=True, raises=True)
        slots.b001(None)  # the panel reports the failure; it must not propagate

        self.assertEqual(len(slots.calls), 1)
        self.assertFalse(slots._apply_btn.isVisible())
        self.assertIsNone(slots._pending)

    def test_a_new_run_supersedes_the_armed_plan(self):
        slots = self._slots(dry_run=True)
        slots.b001(None)
        self.assertTrue(slots._apply_btn.isVisible())

        slots.ui.header.menu.chk_dry_run = _FakeCheck(False)
        slots.b001(None)  # a live run of its own, not the armed plan

        self.assertEqual(len(slots.calls), 2)
        self.assertIs(slots.calls[1]["config"]["dry_run"], False)
        self.assertFalse(slots._apply_btn.isVisible())
        self.assertIsNone(slots._pending)

    def test_an_aborted_run_clears_the_report_and_the_plan(self):
        """ "Nothing selected." is a new run: it supersedes the last preview."""
        slots = self._slots(dry_run=True)
        slots.b001(None)
        cleared_after_preview = slots.ui.txt001.cleared

        slots.ui.cmb_selection_mode.setCurrentText("Selected Objects")
        cmds.select(clear=True)
        slots.b001(None)  # aborts before the engine is reached

        self.assertEqual(len(slots.calls), 1)  # nothing ran
        self.assertGreater(slots.ui.txt001.cleared, cleared_after_preview)
        self.assertFalse(slots._apply_btn.isVisible())
        self.assertIsNone(slots._pending)

    def test_changing_the_selection_mode_disarms(self):
        """The plan was built over the old mode's material set."""
        slots = self._slots(dry_run=True)
        slots.b001(None)
        self.assertTrue(slots._apply_btn.isVisible())

        slots._disarm_apply("Selected Objects")  # what the combo's signal calls

        self.assertFalse(slots._apply_btn.isVisible())
        self.assertIsNone(slots._pending)

    def test_apply_without_a_plan_is_a_no_op(self):
        slots = self._slots(dry_run=True)
        slots._apply_dry_run()
        self.assertEqual(slots.calls, [])


if __name__ == "__main__":
    unittest.main()
