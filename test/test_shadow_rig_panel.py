# !/usr/bin/python
# coding=utf-8
"""Panel-wiring tests for the ``shadow_rig.ui`` panel + ShadowRigSlots.

GUI-only (registered in ``run_tests.GUI_REQUIRED``): the panel hosts a
``Preview`` (Qt), and ``test_preview`` itself native-crashes mayapy in batch.

Where ``test_shadow_rig.py`` covers the engine, this covers what the engine
tests can't see: that the ``.ui`` parses and every widget the slots address
exists (the Utility group collapses, Reset / Create sit outside Options, the
deeper Utility actions hang off option boxes), the preview lifecycle around
the SOURCE (built inside the preview contract it was deleted and recreated
at the default position on every refresh — the reported "preview resets the
source" bug), the reported Source From Selection flows (the source selected
when the preview starts; the button pressed mid-preview; faces building
fixture lights), a failed preview leaving the checkbox unchecked, the
Utility section acting on whatever rig the selection touches, and a rebuilt
rig landing in an isolated viewport (the reported invisible rebuild).
"""

import os
import unittest

import maya.cmds as cmds

import pythontk as ptk
import mayatk as mtk
from mayatk.rig_utils.shadow_rig import ShadowRig
from mayatk.ui_utils.maya_ui_handler import MayaUiHandler
from base_test import MayaTkTestCase


class _PanelCase(MayaTkTestCase):
    """Load the panel once; each test gets a clean scene.

    The silhouettes are written to ``<project>/sourceimages``, and a launched
    GUI Maya opens the user's last project — which can sit on a cloud-synced
    drive that refuses a quick rewrite of a file it is still syncing (errno
    22, seen live on a Dropbox project). The module runs in a scratch project.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ui = MayaUiHandler.instance().get("shadow_rig")
        cls.slots = cls.ui.slots
        cls._artifacts = ptk.TempArtifacts("shadow_rig_panel", policy="scoped")
        project = cls._artifacts.dir_path("project")
        os.makedirs(os.path.join(project, "sourceimages"), exist_ok=True)
        cls._original_workspace = cmds.workspace(q=True, rd=True)
        cmds.workspace(project, openWorkspace=True)

    @classmethod
    def tearDownClass(cls):
        cmds.workspace(cls._original_workspace, openWorkspace=True)
        cls._artifacts.cleanup()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        if self.slots.preview.enabled:
            self.slots.preview.disable()
        self.ui.txt_source.setText(ShadowRig.DEFAULT_SOURCE_NAME)
        self.messages = []
        self._orig_message_box = self.slots.sb.message_box
        self.slots.sb.message_box = self._capture
        self.slots.preview.message_func = self._capture
        self.cube = cmds.polyCube(name="panel_cube", width=2, height=2, depth=2)[0]
        self._textures = []

    def tearDown(self):
        if self.slots.preview.enabled:
            self.slots.preview.disable()
        self.slots.sb.message_box = self._orig_message_box
        self.slots.preview.message_func = self._orig_message_box
        for plane in ShadowRig.find_shadow_planes():
            tex = ShadowRig._plane_texture_path(plane)
            if tex:
                self._textures.append(tex)
        for path in self._textures:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
        super().tearDown()

    def _capture(self, message, *args, **kwargs):
        self.messages.append(str(message))

    def _sun(self, name="panelSunShape"):
        shape = cmds.directionalLight(name=name)
        tf = cmds.listRelatives(shape, parent=True, fullPath=True)[0]
        cmds.setAttr(f"{tf}.rotate", -50, 30, 0, type="double3")
        return tf

    def _enable(self, *objects):
        cmds.select(list(objects) or [self.cube], replace=True)
        self.slots.preview.enable()

    def _option_menu(self, button):
        """The option-box menu of a Utility *button*, the way a user reaches
        it: the Utility group expanded first — a checkable QGroupBox DISABLES
        its children while unchecked (collapsed), and a disabled button's
        ``click()`` is inert — then the menu built the way the switchboard
        builds it (the ``<name>_init`` hook, deferred until the button is
        first shown)."""
        self.ui.groupBox_utility.setChecked(True)
        getattr(self.slots, f"{button.objectName()}_init")(button)
        return button.option_box.menu


class TestPanelSurface(_PanelCase):
    """The .ui parses and every widget the slots address exists."""

    def test_widgets_resolve(self):
        for name in (
            "header",
            "chk_preview",
            "cmb_type",
            "cmb_planes",
            "cmb_atlas",
            "chk_combine",
            "txt_source",
            "s000",
            "s001",
            "chk_follow",
            "b000",
            "b001",
            "b002",
            "b003",
            "b009",
            "b010",
            "groupBox",
            "groupBox_utility",
            "footer",
        ):
            self.assertIsNotNone(getattr(self.ui, name, None), name)

    def test_retired_widgets_are_gone(self):
        """The axis / mode combos (the silhouette is always the projection
        through the source), Sources From Faces (folded into Source From
        Selection) and the buttons now behind option boxes -- Source From
        Selection's b004 among them, an action of Source Name's."""
        for name in ("cmb000", "cmb_mode", "b004", "b005", "b006", "b007", "b008"):
            self.assertIsNone(getattr(self.ui, name, None), name)
        for name in ("b004", "b005", "b006", "b007", "b008"):
            self.assertFalse(hasattr(self.slots, name), name)

    def test_utility_collapses_and_the_actions_sit_outside_options(self):
        from uitk.widgets.collapsableGroup import CollapsableGroup

        self.assertIsInstance(self.ui.groupBox_utility, CollapsableGroup)
        options, utility = self.ui.groupBox, self.ui.groupBox_utility
        for name in ("b000", "b001"):
            parent = getattr(self.ui, name).parent()
            self.assertNotIn(parent, (options, utility), name)
        for name in ("b002", "b003", "b009", "b010"):
            self.assertTrue(utility.isAncestorOf(getattr(self.ui, name)), name)
        for name in (
            "cmb_type",
            "cmb_planes",
            "cmb_atlas",
            "chk_combine",
            "txt_source",
            "s000",
        ):
            self.assertTrue(options.isAncestorOf(getattr(self.ui, name)), name)

    def test_option_boxes_hold_the_deeper_actions(self):
        """Recalculate's option box: Apply Source + Rebuild Rig; Bake's:
        Restore Expression. Building twice adds nothing twice."""
        update = self._option_menu(self.ui.b003)
        self.assertIsNotNone(getattr(update, "btn_apply_source", None))
        self.assertIsNotNone(getattr(update, "btn_rebuild", None))
        bake = self._option_menu(self.ui.b002)
        self.assertIsNotNone(getattr(bake, "btn_restore", None))
        before = len(update.findChildren(type(update.btn_rebuild)))
        self.slots.b003_init(self.ui.b003)
        self.assertEqual(len(update.findChildren(type(update.btn_rebuild))), before)
        # Source Name's: Source From Selection + Reproject, as icon buttons
        field = self.ui.txt_source
        self.slots.txt_source_init(field)
        pick, reproject = field._source_actions
        self.assertEqual(pick.widget.objectName(), "btn_source_from_selection")
        self.assertEqual(reproject.widget.objectName(), "btn_reproject")
        self.assertTrue(field.option_box.container.isAncestorOf(pick.widget))
        self.slots.txt_source_init(field)
        self.assertIs(field._source_actions[0], pick, "built once")
        self.assertTrue(pick.widget.isVisible() or not field.isVisible())

    def test_softness_settles_in_the_ui(self):
        """Softness re-renders every rig its source lights, so the box waits
        for the user to finish: no mid-keystroke value (keyboardTracking
        off) and a debounce declared in the .ui, both twins alike."""
        box = self.ui.s001
        self.assertFalse(box.keyboardTracking())
        self.assertEqual(int(box.property("debounce")), 400)
        self.assertFalse(box.adjusting)

    def test_source_names_parse(self):
        self.ui.txt_source.setText(" keyLight, fill ;fill , ")
        self.assertEqual(self.slots._source_names(), ["keyLight", "fill"])
        self.ui.txt_source.setText("")
        self.assertEqual(self.slots._source_names(), [ShadowRig.DEFAULT_SOURCE_NAME])


class TestRigType(_PanelCase):
    """The Rig combo: Projected is the default, Horizon builds the map; a
    planned item (the suffix mechanism) stays disabled and a programmatic
    selection of one fails the preview cleanly."""

    def setUp(self):
        super().setUp()
        self.ui.cmb_type.setCurrentIndex(0)

    def tearDown(self):
        self.ui.cmb_type.setCurrentIndex(0)
        combo = self.ui.cmb_type
        for i in range(combo.count() - 1, -1, -1):
            if self.slots.PLANNED_SUFFIX in combo.itemText(i):
                combo.removeItem(i)
        super().tearDown()

    def test_projected_is_the_default_and_horizon_is_selectable(self):
        combo = self.ui.cmb_type
        self.assertEqual(combo.currentIndex(), 0)
        self.assertIn("Projected", combo.currentText())
        # Bound classmethods are rebuilt on every access: compare, not `is`.
        self.assertEqual(self.slots._rig_builder(), ShadowRig.create_for_sources)
        self.assertEqual(
            [combo.itemText(i) for i in range(combo.count())],
            ["Rig:  Projected", "Rig:  Horizon"],
        )
        combo.setCurrentIndex(1)
        self.assertEqual(
            self.slots._rig_builder(), ShadowRig.create_horizon_for_sources
        )
        self.slots.cmb_type_init(combo)
        self.assertTrue(all(combo.model().item(i).isEnabled() for i in range(2)))

    def test_planned_items_stay_disabled_and_fail_the_preview(self):
        combo = self.ui.cmb_type
        combo.addItem(f"Rig:  Morphing {self.slots.PLANNED_SUFFIX}")
        self.slots.cmb_type_init(combo)
        self.assertFalse(combo.model().item(combo.count() - 1).isEnabled())
        combo.setCurrentIndex(combo.count() - 1)
        self._enable(self.cube)
        self.assertFalse(self.slots.preview.enabled)
        self.assertFalse(self.ui.chk_preview.isChecked())
        self.assertEqual(ShadowRig.find_shadow_planes(), [])
        self.assertTrue(
            any("not available yet" in m for m in self.messages), self.messages
        )

    def test_horizon_type_builds_the_map_through_the_preview(self):
        self.ui.cmb_type.setCurrentIndex(1)
        self._enable(self.cube)
        self.assertTrue(self.slots.preview.enabled, self.messages)
        plane = "panel_cube_shadow"
        self.assertEqual(ShadowRig.plane_type(plane), "horizon")
        path = ShadowRig._plane_horizon_path(plane)
        self._textures.append(path)
        self.assertTrue(path and os.path.exists(path), path)
        self.slots.preview.finalize_changes()
        self.assertTrue(os.path.exists(path))

    def test_the_preview_box_mirrors_the_scene(self):
        """The Live Horizon Preview box is scene state, never a saved
        setting: disabled with no Horizon rig, enabled once one is
        committed, checked only while a preview stands, and back to
        disabled when the rig is deleted through the panel."""
        from mayatk.rig_utils.shadow_preview import ShadowPreview

        box = self.ui.chk_horizon_preview
        self.slots.chk_horizon_preview_init(box)
        self.assertFalse(box.restore_state, "never restored from QSettings")
        self.assertTrue(box.refresh_on_show, "re-synced on every show")
        self.assertFalse(box.isEnabled(), "no Horizon rig in the scene yet")
        self.assertFalse(box.isChecked())
        self.assertIn("No Horizon rig", box.toolTip())

        self.ui.cmb_type.setCurrentIndex(1)
        self._enable(self.cube)
        self.slots.preview.finalize_changes()
        plane = "panel_cube_shadow"
        path = ShadowRig._plane_horizon_path(plane)
        self._textures.append(path)
        self._textures.append(os.path.splitext(path)[0] + ShadowPreview.TEXTURE_SUFFIX)
        self.assertTrue(box.isEnabled(), "a committed Horizon rig can be previewed")
        self.assertNotIn("No Horizon rig", box.toolTip())
        # A committed Horizon rig shows its live preview at once (the
        # morphing outline is the rig); the box mirrors that.
        self.assertTrue(ShadowPreview.is_attached(plane), self.messages)
        self.assertTrue(box.isChecked(), "the commit attached the preview")

        cmds.select(clear=True)  # the box acts on every Horizon rig then
        box.setChecked(False)  # auto-wired: toggled -> chk_horizon_preview
        self.assertFalse(ShadowPreview.is_attached(plane), self.messages)
        self.assertFalse(box.isChecked())
        box.setChecked(True)
        self.assertTrue(ShadowPreview.is_attached(plane), self.messages)
        self.assertTrue(box.isChecked())

        cmds.select(plane, replace=True)
        self.slots.b009()  # Delete Rig
        self.assertEqual(ShadowRig.find_shadow_planes(), [])
        self.assertEqual(cmds.ls(f"*{ShadowPreview.INFIX}*"), [])
        self.assertFalse(box.isChecked(), "nothing stands")
        self.assertFalse(box.isEnabled(), "nothing to preview")


class TestFollowAndSoftness(_PanelCase):
    """Follow Source arms the engine's watcher; Softness reads and writes
    the named source and Recalculates the rigs it lights."""

    def tearDown(self):
        ShadowRig.auto_recalculate(False)
        super().tearDown()

    @staticmethod
    def _settle(ms=700):
        """Run the event loop long enough for a debounced slot to fire."""
        from qtpy import QtCore

        loop = QtCore.QEventLoop()
        QtCore.QTimer.singleShot(ms, loop.quit)
        getattr(loop, "exec_", loop.exec)()

    def test_reproject_re_renders_the_named_sources_planes(self):
        """Reproject (Source Name's option box): every plane the named source
        lights is re-rendered from where the source is now -- the manual
        form of Follow Source."""
        self._enable(self.cube)
        planes = ShadowRig.find_shadow_planes()
        self.assertEqual(len(planes), 1)
        ShadowRig.auto_recalculate(False)
        source = ShadowRig.DEFAULT_SOURCE_NAME
        cmds.move(-6.0, 8.0, 3.0, source, absolute=True)
        self.assertTrue(ShadowRig.silhouette_is_stale(planes[0], degrees=2.0))
        self.slots.reproject_sources()
        self.assertFalse(ShadowRig.silhouette_is_stale(planes[0], degrees=2.0))
        self.assertIn("Reprojected 1 silhouette", self.messages[-1])
        # a name that lights nothing says so
        self.ui.txt_source.setText("nothingLit")
        self.slots.reproject_sources()
        self.assertIn("No shadow rig is lit by nothingLit", self.messages[-1])

    def test_follow_source_box_arms_the_engine(self):
        box = self.ui.chk_follow
        self.slots.chk_follow_init(box)
        self.assertTrue(box.refresh_on_show, "re-applied on every show")
        self.assertEqual(ShadowRig.auto_recalculate_enabled(), box.isChecked())
        box.setChecked(False)  # auto-wired: toggled -> chk_follow
        self.assertFalse(ShadowRig.auto_recalculate_enabled())
        box.setChecked(True)
        self.assertTrue(ShadowRig.auto_recalculate_enabled())
        self._enable(self.cube)
        self.slots.preview.finalize_changes()
        self.assertIn(
            cmds.ls(ShadowRig.DEFAULT_SOURCE_NAME, long=True)[0],
            ShadowRig._auto_watched,
        )

    def test_softness_box_reads_and_writes_the_named_source(self):
        box = self.ui.s001
        self.slots.s001_init(box)
        self.assertFalse(box.restore_state, "scene state, never a saved setting")
        self.assertTrue(box.refresh_on_show)
        self.assertEqual(box.value(), 0.0, "no source yet")
        self.assertIn("no source yet", box.toolTip())
        # writing creates the source, as Preview would, and stamps it
        box.setValue(0.75)  # auto-wired: valueChanged -> s001, debounced
        source = ShadowRig.DEFAULT_SOURCE_NAME
        self.assertFalse(cmds.objExists(source), "the slot waits for the user")
        self._settle()
        self.assertTrue(cmds.objExists(source))
        self.assertEqual(ShadowRig.source_softness(source), 0.75)
        # the rigs it lights re-render with it
        self._enable(self.cube)
        self.slots.preview.finalize_changes()
        plane = "panel_cube_shadow"
        self.assertAlmostEqual(cmds.getAttr(f"{plane}.sourceSize"), 0.75, places=6)
        box.setValue(1.5)
        self._settle()
        self.assertAlmostEqual(cmds.getAttr(f"{plane}.sourceSize"), 1.5, places=6)
        self.assertEqual(ShadowRig.export_record(plane)["source_size"], 1.5)
        # the box re-reads the source it names: a sun shows degrees
        sun = self._sun()
        cmds.select(sun, replace=True)
        self.slots.source_from_selection()  # Source From Selection -> _on_sources_edited -> sync
        self.assertIn("degrees", box.toolTip())
        self.assertEqual(box.value(), 0.0, "a sun with no Arnold angle is sharp")
        self.ui.txt_source.setText(source)
        self.slots._sync_softness_box()
        self.assertEqual(box.value(), 1.5)
        self.assertIn("its Softness", box.toolTip())


class TestPlanesAndAtlas(_PanelCase):
    """Planes: Per object builds one rig per selected object; Atlas: Auto
    packs them once two exist, Off never; Pack Atlas repacks the scene."""

    def setUp(self):
        super().setUp()
        self.crate = cmds.polyCube(name="panel_crate", width=1, height=1, depth=1)[0]
        cmds.setAttr(f"{self.crate}.translateX", 4.0)
        self.ui.cmb_planes.setCurrentIndex(1)  # Per object
        self.ui.cmb_atlas.setCurrentIndex(0)  # Auto

    def tearDown(self):
        self.ui.cmb_planes.setCurrentIndex(0)
        self.ui.cmb_atlas.setCurrentIndex(0)
        for kind in ShadowRig.ATLAS_BASENAMES:
            path = ShadowRig._atlas_path(kind)
            if os.path.exists(path):
                os.remove(path)
        super().tearDown()

    def _planes(self):
        return sorted(p.split("|")[-1] for p in ShadowRig.find_shadow_planes())

    def test_per_object_with_auto_atlas_packs_the_pair_on_commit(self):
        """The preview builds both planes and leaves the shared atlas alone —
        it is a file the scene's committed rigs sample — and the commit packs
        them."""
        self._enable(self.cube, self.crate)
        self.assertTrue(self.slots.preview.enabled, self.messages)
        self.assertEqual(self._planes(), ["panel_crate_shadow", "panel_cube_shadow"])
        self.assertFalse(
            any(ShadowRig.plane_is_atlased(p) for p in ShadowRig.find_shadow_planes())
        )
        self.assertFalse(os.path.exists(ShadowRig._atlas_path("projected")))
        self.slots.preview.finalize_changes()
        self.assertEqual(len(ShadowRig.find_shadow_planes()), 2)
        self.assertTrue(
            all(ShadowRig.plane_is_atlased(p) for p in ShadowRig.find_shadow_planes())
        )
        self.assertTrue(os.path.exists(ShadowRig._atlas_path("projected")))

    def test_a_cancelled_preview_leaves_a_committed_atlas_intact(self):
        """The hazard the commit-only rule closes: a rehearsal must not
        rewrite (or, on cancel, delete) the atlas the committed rigs read."""
        self._enable(self.cube, self.crate)
        self.slots.preview.finalize_changes()
        atlas = ShadowRig._atlas_path("projected")
        with open(atlas, "rb") as fh:
            before = fh.read()
        third = cmds.polyCube(name="panel_third", width=1, height=1, depth=1)[0]
        cmds.setAttr(f"{third}.translateZ", 5.0)
        self._enable(third)
        self.assertTrue(self.slots.preview.enabled, self.messages)
        self.slots.preview.disable()  # discard
        self.assertTrue(os.path.exists(atlas))
        with open(atlas, "rb") as fh:
            self.assertEqual(fh.read(), before)
        self.assertEqual(len(ShadowRig.find_shadow_planes()), 2)

    def test_atlas_off_keeps_every_plane_on_its_own_png(self):
        self.ui.cmb_atlas.setCurrentIndex(1)  # Off
        self._enable(self.cube, self.crate)
        self.assertTrue(self.slots.preview.enabled, self.messages)
        self.assertFalse(
            any(ShadowRig.plane_is_atlased(p) for p in ShadowRig.find_shadow_planes())
        )
        self.assertFalse(os.path.exists(ShadowRig._atlas_path("projected")))

    def test_a_second_rig_joins_the_first_rigs_atlas(self):
        """Auto packs the SCENE, not just the build: there is one atlas per
        rig type, so the second commit has to bring the first plane in with
        it rather than start an atlas of its own."""
        self.ui.cmb_planes.setCurrentIndex(0)  # Combined
        self._enable(self.cube)
        self.slots.preview.finalize_changes()
        first = ShadowRig.find_shadow_planes()[0]
        self.assertFalse(ShadowRig.plane_is_atlased(first))  # a lone plane
        self._enable(self.crate)
        self.slots.preview.finalize_changes()
        planes = ShadowRig.find_shadow_planes()
        self.assertEqual(len(planes), 2)
        self.assertTrue(
            all(ShadowRig.plane_is_atlased(p) for p in planes),
            [p for p in planes if not ShadowRig.plane_is_atlased(p)],
        )
        rects = [
            tuple(ShadowRig._read_rect(p, ShadowRig._ATLAS_RECT_ATTRS)) for p in planes
        ]
        self.assertEqual(len(set(rects)), 2, rects)  # distinct cells

    def test_auto_leaves_a_lone_plane_alone_and_pack_atlas_packs_it(self):
        self._enable(self.cube)
        self.assertTrue(self.slots.preview.enabled, self.messages)
        self.slots.preview.finalize_changes()
        plane = ShadowRig.find_shadow_planes()[0]
        self.assertFalse(ShadowRig.plane_is_atlased(plane))
        self.slots.b010()
        self.assertTrue(ShadowRig.plane_is_atlased(plane))
        self.assertTrue(
            any("Packed 1 plane" in m for m in self.messages), self.messages
        )
        self.assertTrue(os.path.exists(ShadowRig._atlas_path("projected")))


class TestPreviewSourceRetention(_PanelCase):
    """The source is created at enable, OUTSIDE the contract, and survives
    every refresh and the commit replay wherever the user put it."""

    def test_source_survives_refresh_and_commit(self):
        self._enable()
        self.assertTrue(self.slots.preview.enabled)
        self.assertTrue(cmds.objExists("shadow_source"))
        cmds.setAttr("shadow_source.translate", -3, 8, -4, type="double3")

        self.slots.preview.refresh()
        self.assertEqual(cmds.getAttr("shadow_source.translate")[0], (-3.0, 8.0, -4.0))
        self.assertTrue(cmds.objExists("panel_cube_shadow"))

        self.slots.preview.finalize_changes()
        self.assertFalse(self.slots.preview.enabled)
        self.assertEqual(cmds.getAttr("shadow_source.translate")[0], (-3.0, 8.0, -4.0))
        self.assertTrue(cmds.objExists("panel_cube_shadow"))
        # The committed rig links the source the user positioned.
        _, source = ShadowRig._rig_links("panel_cube_shadow")
        self.assertEqual(source, cmds.ls("shadow_source", long=True)[0])

    def test_cancel_keeps_the_source_and_drops_the_rig(self):
        self._enable()
        self.slots.preview.disable()
        self.assertTrue(cmds.objExists("shadow_source"))
        self.assertFalse(cmds.objExists("panel_cube_shadow"))

    def test_renamed_source_is_created_before_the_refresh(self):
        self._enable()
        self.ui.txt_source.setText("keyLight")
        self.slots._on_sources_edited()
        self.assertTrue(cmds.objExists("keyLight"))
        self.assertTrue(cmds.objExists("panel_cube_keyLight_shadow"))
        # ...and outside the contract: cancelling keeps it.
        self.slots.preview.disable()
        self.assertTrue(cmds.objExists("keyLight"))


class TestSourceFromSelection(_PanelCase):
    """The reported flows around Source From Selection."""

    def test_source_from_selection_writes_the_field(self):
        sun = self._sun()
        cmds.select(sun, replace=True)
        self.slots.source_from_selection()
        self.assertEqual(self.slots._source_names(), [sun])

    def test_source_from_selection_then_preview(self):
        """Pick the light, press the button, select the target, preview: the
        rig is built against the light, projected along its direction."""
        sun = self._sun()
        cmds.select(sun, replace=True)
        self.slots.source_from_selection()
        self._enable(self.cube)
        self.assertTrue(self.slots.preview.enabled)
        planes = ShadowRig.find_shadow_planes()
        self.assertEqual(len(planes), 1)
        _, source = ShadowRig._rig_links(planes[0])
        self.assertEqual(source, sun)
        self.assertTrue(ShadowRig.source_is_directional(source))
        # The plane heads away from the sun (its world -Z), not from a side.
        rig = ShadowRig.from_plane(planes[0])
        (cx, cz), _, _ = rig.current_model().placement(rig.canvas)
        self.assertAlmostEqual(cmds.getAttr(f"{planes[0]}.translateX"), cx, places=3)
        self.assertAlmostEqual(cmds.getAttr(f"{planes[0]}.translateZ"), cz, places=3)

    def test_source_from_selection_during_preview(self):
        """With the preview running, picking a light and pressing the button
        rebuilds the previewed targets against it at once."""
        self._enable(self.cube)
        self.assertTrue(cmds.objExists("panel_cube_shadow"))
        sun = self._sun()
        cmds.select(sun, replace=True)
        self.slots.source_from_selection()
        self.assertTrue(self.slots.preview.enabled)
        self.assertFalse(cmds.objExists("panel_cube_shadow"))
        planes = ShadowRig.find_shadow_planes()
        self.assertEqual(len(planes), 1)
        self.assertEqual(ShadowRig._rig_links(planes[0])[1], sun)
        self.assertEqual(
            ShadowRig._rig_links(planes[0])[0], cmds.ls(self.cube, long=True)
        )

    def test_faces_build_fixture_lights_as_the_sources(self):
        """Selected faces are a fixture (the retired Sources From Faces): an
        area light is built per shape and becomes the source."""
        fixture = cmds.polyCube(name="troffer", width=1.2, height=0.1, depth=0.6)[0]
        cmds.setAttr(f"{fixture}.translateY", 4)
        cmds.select(f"{fixture}.f[3]", replace=True)  # the bottom (lens) face
        self.slots.source_from_selection()
        names = self.slots._source_names()
        self.assertEqual(len(names), 1, names)
        shape = cmds.listRelatives(names[0], shapes=True, fullPath=True)[0]
        self.assertEqual(cmds.nodeType(shape), "areaLight")
        self.assertTrue(any("area light" in m for m in self.messages), self.messages)
        # ...and a plain transform selection is used as it is, as before.
        cmds.select(self.cube, replace=True)
        self.slots.source_from_selection()
        self.assertEqual(self.slots._source_names(), cmds.ls(self.cube, long=True))

    def test_source_in_the_selection_is_not_a_target(self):
        """A selection holding the source alongside the meshes builds the
        shadow for the meshes only."""
        ShadowRig.ensure_source("shadow_source")
        self._enable(self.cube, "shadow_source")
        self.assertTrue(self.slots.preview.enabled)
        self.assertTrue(cmds.objExists("panel_cube_shadow"))
        self.assertEqual(
            ShadowRig._rig_links("panel_cube_shadow")[0], cmds.ls(self.cube, long=True)
        )
        self.assertEqual(len(ShadowRig.find_shadow_planes()), 1)


class TestPreviewFailure(_PanelCase):
    """A preview that cannot build is not a preview: the checkbox unchecks."""

    def test_only_the_source_selected_fails_unchecked(self):
        ShadowRig.ensure_source("shadow_source")
        self._enable("shadow_source")
        self.assertFalse(self.slots.preview.enabled)
        self.assertFalse(self.ui.chk_preview.isChecked())
        self.assertEqual(ShadowRig.find_shadow_planes(), [])
        self.assertTrue(
            any("shadow source itself" in m for m in self.messages), self.messages
        )
        # The source survives (created outside the contract) and is still selected.
        self.assertTrue(cmds.objExists("shadow_source"))
        self.assertEqual(cmds.ls(selection=True), ["shadow_source"])

    def test_meshless_selection_fails_unchecked(self):
        loc = cmds.spaceLocator(name="not_a_mesh")[0]
        self._enable(loc)
        self.assertFalse(self.slots.preview.enabled)
        self.assertFalse(self.ui.chk_preview.isChecked())
        self.assertTrue(any("not_a_mesh" in m for m in self.messages), self.messages)


class TestUtility(_PanelCase):
    """The Utility buttons act on the rig(s) the selection touches."""

    def _commit(self):
        self._enable(self.cube)
        self.slots.preview.finalize_changes()
        self.assertTrue(cmds.objExists("panel_cube_shadow"))
        return "panel_cube_shadow"

    def test_recalculate_via_a_selected_target(self):
        plane = self._commit()
        path = ShadowRig._plane_texture_path(plane)
        before = open(path, "rb").read()
        cmds.setAttr("shadow_source.translate", -6, 4, -2, type="double3")
        cmds.select(self.cube, replace=True)
        self.slots.b003()
        self.assertTrue(
            any("Recalculated 1" in m for m in self.messages), self.messages
        )
        self.assertNotEqual(open(path, "rb").read(), before)

    def test_bake_and_restore_via_the_source(self):
        plane = self._commit()
        cmds.playbackOptions(min=1, max=3)
        cmds.select("shadow_source", replace=True)
        self.slots.b002()
        self.assertTrue(ShadowRig.plane_is_baked(plane))
        self.assertFalse(ShadowRig.plane_is_live(plane))
        self.slots.restore_expression()
        self.assertTrue(ShadowRig.plane_is_live(plane))
        self.assertFalse(ShadowRig.plane_is_baked(plane))

    def test_apply_source_via_the_group(self):
        plane = self._commit()
        sun = self._sun()
        self.ui.txt_source.setText(sun)
        cmds.select("panel_cube_shadow_grp", replace=True)
        self.slots.apply_source()
        self.assertEqual(ShadowRig._rig_links(plane)[1], sun)
        self.assertTrue(ShadowRig.plane_is_live(plane))
        self.assertTrue(any("Source now" in m for m in self.messages), self.messages)

    def test_rebuild_and_delete_via_the_plane(self):
        plane = self._commit()
        cmds.setAttr(f"{self.cube}.scaleY", 2)
        cmds.select(plane, replace=True)
        self.slots.rebuild_rig()
        self.assertTrue(cmds.objExists("panel_cube_shadow"))
        self.assertAlmostEqual(
            cmds.getAttr("panel_cube_shadow.objectHeight"), 4.0, places=3
        )
        cmds.select("panel_cube_shadow", replace=True)
        self.slots.b009()
        self.assertFalse(cmds.objExists("panel_cube_shadow"))
        self.assertTrue(cmds.objExists(self.cube))
        self.assertTrue(cmds.objExists("shadow_source"))

    def test_option_box_buttons_drive_the_deeper_actions(self):
        """The menu buttons are wired to the handlers: Rebuild through
        Recalculate's option box, Restore through Bake's."""
        plane = self._commit()
        cmds.setAttr(f"{self.cube}.scaleY", 2)
        cmds.select(plane, replace=True)
        self._option_menu(self.ui.b003).btn_rebuild.click()
        self.assertAlmostEqual(
            cmds.getAttr("panel_cube_shadow.objectHeight"), 4.0, places=3
        )
        cmds.playbackOptions(min=1, max=3)
        cmds.select("panel_cube_shadow", replace=True)
        self.slots.b002()
        self.assertTrue(ShadowRig.plane_is_baked("panel_cube_shadow"))
        self._option_menu(self.ui.b002).btn_restore.click()
        self.assertTrue(ShadowRig.plane_is_live("panel_cube_shadow"))

    def test_utility_needs_a_rig_in_the_selection(self):
        self._commit()
        cmds.select(clear=True)
        self.slots.b009()
        self.assertTrue(cmds.objExists("panel_cube_shadow"))
        self.assertTrue(
            any("Select the shadow plane" in m for m in self.messages), self.messages
        )
        other = cmds.polyCube(name="unrelated")[0]
        cmds.select(other, replace=True)
        self.slots.apply_source()
        self.assertTrue(
            any("touches no shadow rig" in m for m in self.messages), self.messages
        )

    def test_rebuild_and_a_new_source_join_the_isolation_set(self):
        """The reported gap: a rig rebuilt while a viewport is isolated landed
        invisible. create() and ensure_source() add what they build through
        DisplayUtils.add_to_isolation_set; the Preview's own pass only covers
        the passes it drives."""
        plane = self._commit()
        panel = mtk.UiUtils.get_model_panel()
        self.assertIsNotNone(panel, "needs a visible model panel")
        cmds.select(self.cube, replace=True)
        cmds.isolateSelect(panel, state=1)
        self.addCleanup(cmds.isolateSelect, panel, state=0)
        cmds.isolateSelect(panel, addSelected=1)

        def members():
            iso = cmds.modelEditor(panel, query=True, viewObjects=True)
            return {n.split("|")[-1] for n in (cmds.sets(iso, query=True) or [])}

        self.assertIn("panel_cube", members())
        self.assertNotIn("panel_cube_shadow", members())

        cmds.select(plane, replace=True)
        self.slots.rebuild_rig()
        self.assertTrue(cmds.objExists("panel_cube_shadow"))
        self.assertTrue({"panel_cube_shadow", "panel_cube_shadow_grp"} <= members())

        # A source the panel creates (outside the preview contract) too.
        self.ui.txt_source.setText("iso_source")
        self.slots._ensure_sources()
        self.assertIn("iso_source", members())


if __name__ == "__main__":
    unittest.main(verbosity=2)
