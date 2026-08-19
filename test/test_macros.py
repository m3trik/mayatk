# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.edit_utils.macros module.

The Macros class composes 5 mixins (DisplayMacros, EditMacros, SelectionMacros,
UiMacros, AnimationMacros) on top of MacroManager. Most macro functions are
selection/viewport driven — these tests cover the testable surface:

    - MacroManager.call_with_input (pure parsing)
    - MacroManager.set_macro / set_macros (Maya runtimeCommand)
    - Macros class composition / inheritance
    - Headless-safe macros: m_group, m_combine, m_*_selection (selection masks)
"""

import unittest
from unittest.mock import patch

import maya.cmds as cmds

from mayatk.edit_utils.macros import (
    Macros,
    MacroManager,
    DisplayMacros,
    EditMacros,
    SelectionMacros,
    AnimationMacros,
    UiMacros,
)

from base_test import MayaTkTestCase, QuickTestCase


class TestCallWithInput(QuickTestCase):
    """Pure-Python parsing of input strings into args/kwargs."""

    def test_positional_only(self):
        captured = {}

        def fn(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

        MacroManager.call_with_input(fn, "hello, world")
        self.assertEqual(captured["args"], ("hello", "world"))
        self.assertEqual(captured["kwargs"], {})

    def test_keyword_only(self):
        captured = {}

        def fn(**kwargs):
            captured.update(kwargs)

        MacroManager.call_with_input(fn, "key=1, cat=Display")
        self.assertEqual(captured, {"key": "1", "cat": "Display"})

    def test_mixed_positional_and_keyword(self):
        captured = {"args": (), "kwargs": {}}

        def fn(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

        MacroManager.call_with_input(fn, "macro_name, key=1, cat=Display")
        self.assertEqual(captured["args"], ("macro_name",))
        self.assertEqual(captured["kwargs"], {"key": "1", "cat": "Display"})

    def test_strips_whitespace(self):
        captured = {}

        def fn(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

        MacroManager.call_with_input(fn, "  alpha  ,  key = 5 ")
        self.assertEqual(captured["args"], ("alpha",))
        self.assertEqual(captured["kwargs"], {"key": "5"})


class TestMacrosComposition(QuickTestCase):
    """Macros must inherit all 5 mixins + MacroManager."""

    def test_inherits_macro_manager(self):
        self.assertTrue(issubclass(Macros, MacroManager))

    def test_inherits_all_mixins(self):
        for mixin in (
            DisplayMacros,
            EditMacros,
            SelectionMacros,
            AnimationMacros,
            UiMacros,
        ):
            self.assertTrue(
                issubclass(Macros, mixin),
                f"Macros does not inherit from {mixin.__name__}",
            )

    def test_has_set_macros_classmethod(self):
        self.assertTrue(hasattr(Macros, "set_macros"))
        self.assertTrue(callable(Macros.set_macros))

    def test_representative_macros_present(self):
        # A handful of macros from each mixin
        for name in (
            "m_back_face_culling",
            "m_isolate_selected",
            "m_group",
            "m_combine",
            "m_object_selection",
            "m_vertex_selection",
            "m_edge_selection",
            "m_face_selection",
            "m_set_selected_keys",
            "m_unset_selected_keys",
        ):
            self.assertTrue(
                hasattr(Macros, name), f"Macros missing expected method: {name}"
            )


class TestSetMacro(MayaTkTestCase):
    """MacroManager.set_macro creates Maya runtime commands and hotkeys."""

    TEST_MACRO_NAME = "m_object_selection"

    def tearDown(self):
        # Clean up any runtime command we might have created
        try:
            if cmds.runTimeCommand(self.TEST_MACRO_NAME, exists=True):
                if not cmds.runTimeCommand(
                    self.TEST_MACRO_NAME, query=True, default=True
                ):
                    cmds.runTimeCommand(self.TEST_MACRO_NAME, edit=True, delete=True)
        except Exception:
            pass
        super().tearDown()

    def test_set_macro_creates_runtime_command(self):
        Macros.set_macro(self.TEST_MACRO_NAME, key="F", cat="Test")
        self.assertTrue(
            cmds.runTimeCommand(self.TEST_MACRO_NAME, exists=True),
            "set_macro should create a runtimeCommand",
        )

    def test_set_macros_string_form_creates_command(self):
        Macros.set_macros(f"{self.TEST_MACRO_NAME}, key=G, cat=Test")
        self.assertTrue(cmds.runTimeCommand(self.TEST_MACRO_NAME, exists=True))


@unittest.skipIf(
    cmds.about(batch=True),
    "cmds.selectMode(query=...) is unreliable in batch/standalone mode — it "
    "returns False regardless of the actual mode, so these assertions only "
    "hold in the GUI-connected runner (run_tests.py port mode).",
)
class TestSelectionMacros(MayaTkTestCase):
    """Selection-mode macros — assert the live selectMode/selectType state.

    GUI-only: the macros themselves run headlessly, but the *query* side lies
    under mayapy standalone (see the class skip), so the suite verifies them
    where the query is trustworthy.
    """

    def test_vertex_selection_sets_component_mode(self):
        cube = cmds.polyCube(name="sel_cube")[0]
        cmds.select(cube)
        SelectionMacros.m_vertex_selection()
        self.assertTrue(cmds.selectMode(query=True, component=True))
        self.assertTrue(cmds.selectType(query=True, vertex=True))

    def test_edge_selection_sets_component_mode(self):
        cube = cmds.polyCube(name="edge_cube")[0]
        cmds.select(cube)
        SelectionMacros.m_edge_selection()
        self.assertTrue(cmds.selectMode(query=True, component=True))
        self.assertTrue(cmds.selectType(query=True, edge=True))

    def test_face_selection_sets_component_mode(self):
        cube = cmds.polyCube(name="face_cube")[0]
        cmds.select(cube)
        SelectionMacros.m_face_selection()
        self.assertTrue(cmds.selectMode(query=True, component=True))
        self.assertTrue(cmds.selectType(query=True, facet=True))

    def test_object_selection_returns_to_object_mode(self):
        SelectionMacros.m_object_selection()
        # selectMode should be in object mode after the call
        self.assertTrue(cmds.selectMode(query=True, object=True))


class TestEditMacros(MayaTkTestCase):
    """EditMacros — geometry-affecting headless-safe ones."""

    def test_m_group_creates_group(self):
        cube = cmds.polyCube(name="grp_cube")[0]
        cmds.select(cube)
        result = EditMacros.m_group()
        # Result should be a group containing the cube
        self.assertIsNotNone(result)

    def test_m_combine_two_cubes_yields_single_mesh(self):
        a = cmds.polyCube(name="comb_a")[0]
        b = cmds.polyCube(name="comb_b")[0]
        cmds.move(3, 0, 0, b)
        before = len(cmds.ls(type="mesh"))

        EditMacros.m_combine(objects=[a, b])

        after = len(cmds.ls(type="mesh"))
        # Combine collapses two meshes into one (count may include shape nodes)
        self.assertLessEqual(after, before)


class TestCycleDisplayState(MayaTkTestCase):
    """m_cycle_display_state expands a selected group to its leaf children.

    ``cmds.displaySurface`` only accepts nodes with a surface shape below them —
    a locator / joint / curve leaf raises "No surfaces selected", which used to
    abort the whole macro when a mixed group was selected.
    """

    def _make_mixed_group(self):
        cube = cmds.polyCube(name="cyc_cube")[0]
        loc = cmds.spaceLocator(name="cyc_loc")[0]
        grp = cmds.group(cube, loc, name="cyc_grp")
        return cube, loc, grp

    def test_group_with_non_surface_child_does_not_raise(self):
        cube, loc, grp = self._make_mixed_group()
        cmds.select(grp, replace=True)

        DisplayMacros.m_cycle_display_state()  # must not raise

        self.assertTrue(cmds.displaySurface(cube, xRay=True, query=True)[0])

    def test_full_cycle_returns_to_visible(self):
        cube, loc, grp = self._make_mixed_group()
        cmds.select(grp, replace=True)

        for _ in range(4):  # Visible -> XRay -> Templated -> Hidden -> Visible
            DisplayMacros.m_cycle_display_state()

        self.assertTrue(cmds.getAttr(f"{cube}.visibility"))
        self.assertFalse(cmds.getAttr(f"{cube}.template"))
        self.assertFalse(cmds.displaySurface(cube, xRay=True, query=True)[0])

    def test_non_surface_children_still_hide(self):
        """The x-ray leg is surface-only, but hide/template must reach every leaf."""
        cube, loc, grp = self._make_mixed_group()
        cmds.select(grp, replace=True)

        for _ in range(3):  # Visible -> XRay -> Templated -> Hidden
            DisplayMacros.m_cycle_display_state()

        self.assertFalse(cmds.getAttr(f"{cube}.visibility"))
        self.assertFalse(cmds.getAttr(f"{loc}.visibility"))

    def test_group_of_non_surfaces_still_cycles(self):
        """With nothing to x-ray the cycle skips that leg rather than stalling."""
        a = cmds.spaceLocator(name="cyc_loc_a")[0]
        b = cmds.spaceLocator(name="cyc_loc_b")[0]
        grp = cmds.group(a, b, name="cyc_loc_grp")
        cmds.select(grp, replace=True)

        DisplayMacros.m_cycle_display_state()
        self.assertTrue(cmds.getAttr(f"{a}.template"))

        DisplayMacros.m_cycle_display_state()
        self.assertFalse(cmds.getAttr(f"{a}.visibility"))

    def test_component_selection_cycles_the_owning_object(self):
        cube = cmds.polyCube(name="cyc_comp_cube")[0]
        cmds.select(f"{cube}.f[0:2]", replace=True)

        DisplayMacros.m_cycle_display_state()

        self.assertTrue(cmds.displaySurface(cube, xRay=True, query=True)[0])

    def test_object_set_selection_cycles_its_members(self):
        """An outliner-selected set node used to raise on ``<set>.visibility``."""
        cube = cmds.polyCube(name="cyc_set_cube")[0]
        s = cmds.sets([cube], name="cyc_bake_set")
        cmds.select(s, replace=True, noExpand=True)  # the set node itself

        DisplayMacros.m_cycle_display_state()  # must not raise

        self.assertTrue(cmds.displaySurface(cube, xRay=True, query=True)[0])

    def test_non_dag_member_is_skipped(self):
        """A set holding a material must not strand the cycle on a bad probe."""
        cube = cmds.polyCube(name="cyc_sg_cube")[0]
        shader = cmds.shadingNode("lambert", asShader=True, name="cyc_lambert")
        s = cmds.sets([cube, shader], name="cyc_mixed_set")
        cmds.select(s, replace=True, noExpand=True)

        DisplayMacros.m_cycle_display_state()  # must not raise

        self.assertTrue(cmds.displaySurface(cube, xRay=True, query=True)[0])

    def test_locked_visibility_child_does_not_abort_siblings(self):
        cube = cmds.polyCube(name="cyc_locked_cube")[0]
        other = cmds.polyCube(name="cyc_other_cube")[0]
        grp = cmds.group(cube, other, name="cyc_locked_grp")
        cmds.setAttr(f"{cube}.visibility", lock=True)
        cmds.select(grp, replace=True)

        for _ in range(3):  # ... -> Hidden
            DisplayMacros.m_cycle_display_state()

        self.assertFalse(cmds.getAttr(f"{other}.visibility"))


class TestSmoothPreview(MayaTkTestCase):
    """m_smooth_preview resolves the selection to mesh shapes.

    ``displaySmoothMesh`` is a mesh attribute — a curve / locator / light in the
    selection used to raise "No object matches name: <node>.displaySmoothMesh"
    before any mesh in the same selection got toggled.
    """

    def setUp(self):
        super().setUp()
        # The wireframe pref is global and outlives the per-test scene reset.
        self._wireframe = cmds.displayPref(query=True, wireframeOnShadedActive=True)
        self.addCleanup(
            lambda: cmds.displayPref(wireframeOnShadedActive=self._wireframe)
        )

    def test_mixed_selection_does_not_raise(self):
        cube = cmds.polyCube(name="smp_cube")[0]
        curve = cmds.curve(name="smp_curve", degree=1, point=[(0, 0, 0), (1, 0, 0)])
        cmds.select([cube, curve], replace=True)

        DisplayMacros.m_smooth_preview()  # must not raise

        self.assertEqual(cmds.getAttr(f"{cube}.displaySmoothMesh"), 2)

    def test_group_with_non_mesh_child_toggles_the_mesh(self):
        cube = cmds.polyCube(name="smp_grp_cube")[0]
        loc = cmds.spaceLocator(name="smp_grp_loc")[0]
        grp = cmds.group(cube, loc, name="smp_grp")
        cmds.select(grp, replace=True)

        DisplayMacros.m_smooth_preview()

        self.assertEqual(cmds.getAttr(f"{cube}.displaySmoothMesh"), 2)

    def test_non_mesh_only_selection_is_a_no_op(self):
        curve = cmds.curve(name="smp_only_curve", degree=1, point=[(0, 0, 0), (1, 0, 0)])
        cmds.select(curve, replace=True)

        DisplayMacros.m_smooth_preview()  # must not raise

    def test_toggles_back_off(self):
        cube = cmds.polyCube(name="smp_toggle_cube")[0]
        cmds.select(cube, replace=True)

        for _ in range(3):  # off -> on/no-wire -> on/full-wire -> off
            DisplayMacros.m_smooth_preview()

        self.assertEqual(cmds.getAttr(f"{cube}.displaySmoothMesh"), 0)

    def test_multiple_meshes_cycle_in_lockstep(self):
        """The wireframe pref is global, so the state is decided once for all.

        Deciding per object inside the loop let iteration N read the pref
        iteration N-1 had just written — the second press over two meshes left
        the first smoothed and turned the second one off.
        """
        cubes = [cmds.polyCube(name=f"smp_multi_{i}")[0] for i in range(3)]
        cmds.displayPref(wireframeOnShadedActive="full")
        cmds.select(cubes, replace=True)

        for expected in (2, 2, 0, 2):  # on/no-wire -> on/full-wire -> off -> on
            DisplayMacros.m_smooth_preview()
            states = [cmds.getAttr(f"{c}.displaySmoothMesh") for c in cubes]
            self.assertEqual(states, [expected] * len(cubes))

    def test_subd_comps_reset_on_the_next_lap(self):
        """Left set, the subdivided components stay drawn through the next cycle."""
        cube = cmds.polyCube(name="smp_subd_cube")[0]
        cmds.select(cube, replace=True)

        for _ in range(2):  # off -> on/no-wire -> on/full-wire
            DisplayMacros.m_smooth_preview()
        self.assertTrue(cmds.getAttr(f"{cube}.displaySubdComps"))

        for _ in range(2):  # -> off -> on/no-wire
            DisplayMacros.m_smooth_preview()
        self.assertFalse(cmds.getAttr(f"{cube}.displaySubdComps"))


class TestGridMacros(MayaTkTestCase):
    """m_grid toggles the grid on its own; m_grid_and_image_planes drives that same
    toggle (the grid LEADS) and syncs image planes to it — mirroring blendertk, whose
    counterpart has always led with the grid.

    ``cmds.grid`` is a display pref, not scene state, so it survives ``file(new=True)``
    between tests — tearDown restores Maya's default-on grid.
    """

    def tearDown(self):
        try:
            cmds.grid(toggle=True)
        except Exception:
            pass
        super().tearDown()

    def test_m_grid_toggles_off_then_back_on(self):
        cmds.grid(toggle=True)
        Macros.m_grid()
        self.assertFalse(
            cmds.grid(query=True, toggle=True), "m_grid did not turn the grid OFF"
        )
        Macros.m_grid()
        self.assertTrue(
            cmds.grid(query=True, toggle=True), "m_grid did not turn the grid back ON"
        )

    def test_m_grid_returns_the_applied_state(self):
        # The return value is what lets m_grid_and_image_planes follow it (DRY).
        cmds.grid(toggle=True)
        self.assertIs(Macros.m_grid(), False)
        self.assertIs(Macros.m_grid(), True)

    def test_grid_toggles_with_no_image_planes(self):
        # REGRESSION: cmds.grid(toggle=...) used to sit INSIDE `for obj in image_plane:`,
        # so an empty scene (no image planes — i.e. most scenes) left the grid untouched
        # and the macro silently did nothing at all.
        self.assertEqual(
            cmds.ls(exactType="imagePlane"),
            [],
            "fixture must start with no image planes",
        )
        cmds.grid(toggle=True)
        Macros.m_grid_and_image_planes()
        self.assertFalse(
            cmds.grid(query=True, toggle=True),
            "m_grid_and_image_planes must toggle the grid even with no image planes",
        )

    def test_grid_leads_and_image_planes_follow(self):
        cmds.imagePlane()
        plane = cmds.ls(exactType="imagePlane")[0]  # the node the macro itself resolves
        cmds.grid(toggle=True)

        Macros.m_grid_and_image_planes()  # grid ON -> OFF, planes follow it off
        self.assertFalse(cmds.grid(query=True, toggle=True))
        self.assertEqual(cmds.getAttr(f"{plane}.displayMode"), 0)

        Macros.m_grid_and_image_planes()  # grid OFF -> ON, planes follow it on
        self.assertTrue(cmds.grid(query=True, toggle=True))
        self.assertEqual(cmds.getAttr(f"{plane}.displayMode"), 2)


class TestMacroDiscovery(QuickTestCase):
    """Every public m_* method declared on a mixin must resolve on Macros.

    Catches the class of bug where a hotkey/runtimeCommand is registered for a
    name that no longer exists on the class (rename, deletion, typo).
    """

    MIXINS = (DisplayMacros, EditMacros, SelectionMacros, AnimationMacros, UiMacros)

    @classmethod
    def all_macro_names(cls):
        names = set()
        for mixin in cls.MIXINS:
            for n, v in vars(mixin).items():
                if n.startswith("m_") and callable(
                    v.__func__ if isinstance(v, (staticmethod, classmethod)) else v
                ):
                    names.add(n)
        return sorted(names)

    def test_every_mixin_macro_resolves_on_Macros(self):
        missing = [n for n in self.all_macro_names() if not hasattr(Macros, n)]
        self.assertFalse(
            missing, f"Macros class is missing methods declared on mixins: {missing}"
        )

    def test_every_macro_is_callable(self):
        non_callable = [
            n for n in self.all_macro_names() if not callable(getattr(Macros, n))
        ]
        self.assertFalse(non_callable, f"Non-callable m_* attributes: {non_callable}")


class TestMacroSmokeInvocation(MayaTkTestCase):
    """Invoke every m_* macro on a fresh selection to catch latent code bugs.

    We tolerate failures that depend on a real viewport (RuntimeError from
    missing modelPanel, KeyError from MEL globals, etc.) but FAIL on the
    AttributeError/NameError/TypeError-on-string class — exactly the bug
    pattern produced by leftover PyMel attribute access on string node names.
    """

    # Bugs we want to catch; everything else is tolerated as environment-related.
    FATAL = (AttributeError, NameError)

    # Macros that legitimately need user input or destructive scene state we
    # don't want to set up generically. Skipped from invocation but still
    # discovered/required to exist by TestMacroDiscovery.
    SKIP_INVOCATION = {
        "m_paste_and_rename",  # depends on the cut/copy buffer
        "m_boolean",  # needs >=2 specific meshes
        "m_toggle_panels",  # needs main Maya window (Qt) — None in mayapy
    }

    def _selected_cube(self, name="smoke_cube"):
        cube = cmds.polyCube(name=name)[0]
        cmds.select(cube, replace=True)
        return cube

    def test_every_macro_invokes_without_python_bug(self):
        names = TestMacroDiscovery.all_macro_names()
        bug_failures = []

        for name in names:
            if name in self.SKIP_INVOCATION:
                continue
            cmds.file(new=True, force=True)
            self._selected_cube()
            fn = getattr(Macros, name)
            try:
                fn()
            except self.FATAL as e:
                bug_failures.append(f"{name}: {type(e).__name__}: {e}")
            except Exception:
                # Tolerated: runtime/UI/state errors that depend on a real
                # viewport, focused panel, or MEL globals. Not what we're
                # testing here.
                pass

        self.assertFalse(
            bug_failures,
            "Macros raised Python-level bugs (likely PyMel-on-string or "
            "missing-symbol):\n  " + "\n  ".join(bug_failures),
        )


class TestMacroRegistration(MayaTkTestCase):
    """set_macro must succeed for every discovered macro name.

    Mirrors the real registration path the user invokes from userSetup.py;
    any name that fails here would also fail at startup.
    """

    def tearDown(self):
        for name in TestMacroDiscovery.all_macro_names():
            try:
                if cmds.runTimeCommand(name, exists=True) and not cmds.runTimeCommand(
                    name, query=True, default=True
                ):
                    cmds.runTimeCommand(name, edit=True, delete=True)
            except Exception:
                pass
        super().tearDown()

    def test_register_every_macro(self):
        failures = []
        for i, name in enumerate(TestMacroDiscovery.all_macro_names()):
            try:
                # Use F-key slots to avoid clobbering common shortcuts.
                Macros.set_macro(name, key=f"F{(i % 12) + 1}", cat="SmokeTest")
            except Exception as e:
                failures.append(f"{name}: {type(e).__name__}: {e}")
        self.assertFalse(failures, "set_macro failed for:\n  " + "\n  ".join(failures))


class TestKeyFormatConversion(QuickTestCase):
    """Pure Maya<->Qt key-token conversion + canonicalisation."""

    def test_qt_to_maya_modifiers(self):
        self.assertEqual(Macros.qt_sequence_to_maya_key("Ctrl+Shift+I"), "ctl+sht+i")
        self.assertEqual(Macros.qt_sequence_to_maya_key("Alt+S"), "alt+s")

    def test_maya_to_qt_modifiers(self):
        self.assertEqual(Macros.maya_key_to_qt_sequence("ctl+sht+i"), "Ctrl+Shift+I")
        self.assertEqual(Macros.maya_key_to_qt_sequence("alt+s"), "Alt+S")

    def test_function_key_passthrough(self):
        self.assertEqual(Macros.qt_sequence_to_maya_key("F3"), "F3")
        self.assertEqual(Macros.maya_key_to_qt_sequence("F3"), "F3")

    def test_round_trip_is_stable(self):
        for token in ("1", "f", "ctl+g", "alt+ctl+s", "ctl+sht+i", "F5"):
            self.assertEqual(
                Macros._normalize_key(
                    Macros.qt_sequence_to_maya_key(
                        Macros.maya_key_to_qt_sequence(token)
                    )
                ),
                Macros._normalize_key(token),
            )

    def test_normalize_is_order_independent(self):
        self.assertEqual(
            Macros._normalize_key("sht+ctl+i"), Macros._normalize_key("ctl+sht+i")
        )

    def test_empty_inputs(self):
        self.assertEqual(Macros.qt_sequence_to_maya_key(""), "")
        self.assertEqual(Macros.maya_key_to_qt_sequence(""), "")


class TestFindConflicts(QuickTestCase):
    """Duplicate-hotkey detection over a binding set."""

    def test_detects_duplicate_key(self):
        bindings = {
            "a": {"key": "1"},
            "b": {"key": "1"},
            "c": {"key": "2"},
        }
        conflicts = Macros.find_conflicts(bindings)
        self.assertIn("1", conflicts)
        self.assertCountEqual(conflicts["1"], ["a", "b"])
        self.assertNotIn("2", conflicts)

    def test_modifier_order_collides(self):
        bindings = {"a": {"key": "ctl+sht+i"}, "b": {"key": "sht+ctl+i"}}
        conflicts = Macros.find_conflicts(bindings)
        self.assertEqual(len(conflicts), 1)

    def test_no_conflicts_in_default_preset(self):
        defaults = Macros.load_preset(Macros.DEFAULT_PRESET)
        self.assertEqual(Macros.find_conflicts(defaults), {})


class TestListAvailableMacros(QuickTestCase):
    """Macro discovery for the UI table."""

    def test_discovers_macros(self):
        macros = Macros.list_available_macros()
        self.assertGreater(len(macros), 0)
        self.assertIn("m_wireframe", macros)

    def test_excludes_non_macro_methods(self):
        macros = Macros.list_available_macros()
        self.assertNotIn("set_macro", macros)
        self.assertNotIn("apply_bindings", macros)

    def test_annotation_is_first_docline(self):
        macros = Macros.list_available_macros()
        self.assertTrue(macros["m_wireframe"])  # non-empty annotation
        self.assertNotIn("\n", macros["m_wireframe"])


class TestMacroPresentation(QuickTestCase):
    """Human-readable labels + docstring-sourced help for the UI."""

    def test_label_humanizes_name(self):
        self.assertEqual(Macros.macro_label("m_back_face_culling"), "Back Face Culling")

    def test_label_preserves_acronyms(self):
        self.assertEqual(
            Macros.macro_label("m_toggle_UV_select_type"), "Toggle UV Select Type"
        )
        self.assertEqual(
            Macros.macro_label("m_component_id_display"), "Component ID Display"
        )

    def test_help_is_full_docstring(self):
        help_text = Macros.macro_help("m_wireframe")
        self.assertTrue(help_text)
        self.assertIn("wireframe", help_text.lower())

    def test_help_missing_macro_is_empty(self):
        self.assertEqual(Macros.macro_help("m_does_not_exist"), "")

    def test_every_macro_has_a_default_category(self):
        # The defining *Macros mixin is the SSoT, so no macro is uncategorized.
        uncategorized = [
            name
            for name in Macros.list_available_macros()
            if not Macros.macro_category(name)
        ]
        self.assertEqual(uncategorized, [])

    def test_category_derives_from_defining_mixin(self):
        cases = {
            "m_wireframe": "Display",  # DisplayMacros
            "m_group": "Edit",  # EditMacros
            "m_object_selection": "Selection",  # SelectionMacros
            "m_set_selected_keys": "Animation",  # AnimationMacros
            "m_toggle_panels": "UI",  # UiMacros (acronym preserved)
        }
        for name, cat in cases.items():
            self.assertEqual(Macros.macro_category(name), cat, name)

    def test_category_missing_macro_is_empty(self):
        self.assertEqual(Macros.macro_category("m_does_not_exist"), "")

    def test_list_categories_matches_derived_set(self):
        self.assertEqual(
            Macros.list_categories(),
            ["Animation", "Display", "Edit", "Selection", "UI"],
        )

    def test_default_preset_categories_match_mixin(self):
        # Shipped default bindings must agree with the code's category SSoT
        # (no Edit/Selection-style drift between bound + unbound siblings).
        defaults = Macros.load_preset(Macros.DEFAULT_PRESET)
        for name, spec in defaults.items():
            self.assertEqual(spec.get("cat"), Macros.macro_category(name), name)


class _NoPrefsFlush:
    """Mixin: block ``cmds.savePrefs`` for the test.

    The test harness runs against the developer's REAL Maya prefs (no
    ``MAYA_APP_DIR`` sandbox), so an integration test that drives the real
    ``apply_bindings`` would otherwise flush its throwaway hotkey bindings
    (F7/F8/F9, ...) into the user's ``userHotkeys_*.mel``. That pollution is
    exactly what seeded the stale chords the launch-time preset re-apply then
    fought (and re-saved) on every launch. In-memory hotkey edits are fine —
    the test Maya is force-closed, so nothing persists unless savePrefs runs.
    """

    def setUp(self):
        super().setUp()
        from unittest import mock

        from mayatk.edit_utils import macros

        patcher = mock.patch.object(macros.cmds, "savePrefs", create=True)
        patcher.start()
        self.addCleanup(patcher.stop)


class _TempPresetRoot:
    """Mixin: redirect the shared preset root to a throwaway dir per test."""

    def setUp(self):
        super().setUp()
        import os
        import tempfile

        self._preset_tmp = tempfile.mkdtemp(prefix="macro_presets_")
        self._prev_root = os.environ.get("UITK_PRESETS_ROOT")
        os.environ["UITK_PRESETS_ROOT"] = os.path.join(self._preset_tmp, "uitk")

    def tearDown(self):
        import os
        import shutil

        if self._prev_root is None:
            os.environ.pop("UITK_PRESETS_ROOT", None)
        else:
            os.environ["UITK_PRESETS_ROOT"] = self._prev_root
        shutil.rmtree(self._preset_tmp, ignore_errors=True)
        super().tearDown()


class TestPresetRoundTrip(_TempPresetRoot, QuickTestCase):
    """PresetStore-backed persistence (no Maya required)."""

    def test_builtin_default_is_listed_and_readonly(self):
        self.assertIn(Macros.DEFAULT_PRESET, Macros.list_presets())
        # Built-ins cannot be deleted.
        self.assertFalse(Macros.delete_preset(Macros.DEFAULT_PRESET))

    def test_load_preset_strips_meta(self):
        Macros.save_preset(
            "unittest_meta", {"m_wireframe": {"key": "3", "cat": "Display"}}
        )
        try:
            data = Macros.load_preset("unittest_meta")
            self.assertNotIn("_meta", data)
            self.assertIn("m_wireframe", data)
        finally:
            Macros.delete_preset("unittest_meta")

    def test_shipped_default_is_all_unbound(self):
        # Contract: the shipped 'default' preset carries NO bindings — loading
        # it clears every macro hotkey. Bindings are opt-in via user presets.
        self.assertEqual(Macros.load_preset(Macros.DEFAULT_PRESET), {})

    def test_save_load_delete_round_trip(self):
        bindings = {
            "m_wireframe": {"key": "3", "cat": "Display"},
            "m_group": {"key": "ctl+g", "cat": "Edit"},
        }
        Macros.save_preset("unittest_set", bindings)
        self.assertIn("unittest_set", Macros.list_presets())
        self.assertEqual(Macros.get_active_preset(), "unittest_set")
        self.assertEqual(Macros.load_preset("unittest_set"), bindings)
        self.assertTrue(Macros.delete_preset("unittest_set"))
        self.assertNotIn("unittest_set", Macros.list_presets())


class TestApplyBindings(_NoPrefsFlush, _TempPresetRoot, MayaTkTestCase):
    """apply_bindings / set_macros parity + clear/unset (needs Maya)."""

    NAMES = ("m_wireframe", "m_group", "m_object_selection")

    def tearDown(self):
        for name in self.NAMES:
            try:
                if cmds.runTimeCommand(name, exists=True) and not cmds.runTimeCommand(
                    name, query=True, default=True
                ):
                    cmds.runTimeCommand(name, edit=True, delete=True)
            except Exception:
                pass
        super().tearDown()

    def test_apply_bindings_registers_commands(self):
        Macros.apply_bindings(
            {
                "m_wireframe": {"key": "F7", "cat": "Display"},
                "m_group": {"key": "F8", "cat": "Edit"},
            }
        )
        self.assertTrue(cmds.runTimeCommand("m_wireframe", exists=True))
        self.assertEqual(
            cmds.runTimeCommand("m_wireframe", query=True, category=True), "Display"
        )

    def test_apply_bindings_matches_set_macros(self):
        Macros.apply_bindings({"m_object_selection": {"key": "F9", "cat": "Edit"}})
        cat_a = cmds.runTimeCommand("m_object_selection", query=True, category=True)
        cmds.runTimeCommand("m_object_selection", edit=True, delete=True)
        Macros.set_macros("m_object_selection, key=F9, cat=Edit")
        cat_b = cmds.runTimeCommand("m_object_selection", query=True, category=True)
        self.assertEqual(cat_a, cat_b)

    def test_unset_macro_removes_command(self):
        Macros.set_macro("m_group", key="F10", cat="Edit")
        Macros.unset_macro("m_group", key="F10")
        self.assertFalse(cmds.runTimeCommand("m_group", exists=True))


class TestLiveHotkeyIntrospection(QuickTestCase):
    """assignCommand keyString -> Maya token conversion (live map is GUI-only)."""

    def test_keystring_to_token_modifiers(self):
        from mayatk.ui_utils.hotkey_collisions import HotkeyCollisions

        # Maya 2025 7-element keyString: [key, alt, ctrl, ?, shift, ?, ?]
        self.assertEqual(
            HotkeyCollisions.keystring_to_token(["i", "0", "1", "0", "0", "0", "0"]),
            "ctl+i",
        )
        self.assertEqual(
            HotkeyCollisions.keystring_to_token(["g", "0", "1", "0", "0", "0", "0"]),
            "ctl+g",
        )

    def test_keystring_uppercase_letter_implies_shift(self):
        from mayatk.ui_utils.hotkey_collisions import HotkeyCollisions

        # Upper-case glyph with ctrl flag -> ctl+sht+i (canonical token form).
        token = HotkeyCollisions.keystring_to_token(["I", "0", "1", "0", "0", "0", "0"])
        self.assertEqual(
            Macros._normalize_key(token), Macros._normalize_key("ctl+sht+i")
        )

    def test_keystring_function_key_passthrough(self):
        from mayatk.ui_utils.hotkey_collisions import HotkeyCollisions

        self.assertEqual(
            HotkeyCollisions.keystring_to_token(["F3", "0", "0", "0", "0", "0", "0"]),
            "F3",
        )

    def test_empty_keystring(self):
        from mayatk.ui_utils.hotkey_collisions import HotkeyCollisions

        self.assertEqual(HotkeyCollisions.keystring_to_token([]), "")

    def test_live_map_empty_when_registry_unavailable(self):
        # When assignCommand reports no elements (None in mayapy standalone),
        # the map is empty and never crashes. Mock the count so the assertion
        # is deterministic regardless of whether the running Maya happens to
        # have its default hotkeys loaded (a GUI-connected runner does).
        from unittest import mock
        from mayatk.ui_utils import hotkey_collisions

        with mock.patch.object(
            hotkey_collisions.cmds, "assignCommand", return_value=None
        ):
            self.assertEqual(hotkey_collisions.HotkeyCollisions.live_hotkey_map(), {})


class TestEnsureEditableHotkeySet(QuickTestCase):
    """set_macro's locked-set guard: Maya refuses hotkey edits while the
    factory ``Maya_Default`` set is current, so an editable user set must be
    made current first (hotkey sets are GUI-only — mocked here; the live-GUI
    behavior was verified end-to-end 2026-07-10)."""

    @staticmethod
    def _fake_hotkeySet(current, existing):
        """A stateful ``cmds.hotkeySet`` stand-in covering the query /
        create / switch calls ``ensure_editable_hotkey_set`` makes."""
        state = {"current": current, "sets": list(existing)}

        def fake(*args, **kw):
            if kw.get("query"):
                if kw.get("current"):
                    return state["current"]
                if kw.get("exists"):
                    return args[0] in state["sets"]
                if kw.get("hotkeySetArray"):
                    return list(state["sets"])
                return None
            if kw.get("edit"):
                if kw.get("current"):
                    state["current"] = args[0]
                if kw.get("delete"):
                    state["sets"].remove(args[0])
                return None
            state["sets"].append(args[0])  # create
            if kw.get("current"):
                state["current"] = args[0]
            return args[0]

        return fake, state

    def test_locked_factory_set_creates_and_switches(self):
        from unittest import mock
        from mayatk.ui_utils import hotkey_collisions as hc

        fake, state = self._fake_hotkeySet("Maya_Default", ["Maya_Default"])
        with mock.patch.object(hc.cmds, "hotkeySet", side_effect=fake):
            name = hc.HotkeyCollisions.ensure_editable_hotkey_set()
        self.assertEqual(name, hc.MACRO_HOTKEY_SET)
        self.assertEqual(state["current"], hc.MACRO_HOTKEY_SET)
        self.assertIn(hc.MACRO_HOTKEY_SET, state["sets"])

    def test_existing_user_set_reused_not_duplicated(self):
        from unittest import mock
        from mayatk.ui_utils import hotkey_collisions as hc

        fake, state = self._fake_hotkeySet(
            "Maya_Default", ["Maya_Default", hc.MACRO_HOTKEY_SET]
        )
        with mock.patch.object(hc.cmds, "hotkeySet", side_effect=fake):
            name = hc.HotkeyCollisions.ensure_editable_hotkey_set()
        self.assertEqual(name, hc.MACRO_HOTKEY_SET)
        self.assertEqual(state["current"], hc.MACRO_HOTKEY_SET)
        self.assertEqual(state["sets"].count(hc.MACRO_HOTKEY_SET), 1)

    def test_editable_current_set_untouched(self):
        from unittest import mock
        from mayatk.ui_utils import hotkey_collisions as hc

        fake, state = self._fake_hotkeySet("MySet", ["Maya_Default", "MySet"])
        with mock.patch.object(hc.cmds, "hotkeySet", side_effect=fake):
            name = hc.HotkeyCollisions.ensure_editable_hotkey_set()
        self.assertEqual(name, "MySet")
        self.assertEqual(state["current"], "MySet")
        self.assertNotIn(hc.MACRO_HOTKEY_SET, state["sets"])


class TestApplyBindingsResilience(QuickTestCase):
    """apply_bindings: one bad chord logs and continues — it must never abort
    the rest of the preset (pre-fix, the first raising entry killed the whole
    startup / manual preset apply)."""

    def test_one_bad_entry_does_not_abort_the_rest(self):
        from unittest import mock
        from mayatk.edit_utils import macros

        applied = []

        def fake_set_macro(name, key=None, cat=None):
            if name == "m_bad":
                raise RuntimeError("boom")
            applied.append(name)

        # apply_bindings diffs against the live registry, so stub it: m_bad and
        # m_group are unbound (forcing set_macro), m_unbind is currently bound
        # (forcing clear_hotkey). The bad entry must still not abort the rest.
        # savePrefs is mocked so this unit test never flushes the real prefs.
        live = {"m_unbind": {"key": "F4", "cat": "Edit"}}
        with (
            mock.patch.object(Macros, "get_current_bindings", return_value=live),
            mock.patch.object(
                Macros,
                "_key_bound_to",
                return_value=False,  # targets not yet live
            ),
            mock.patch.object(Macros, "set_macro", side_effect=fake_set_macro),
            mock.patch.object(Macros, "clear_hotkey") as clear,
            mock.patch.object(macros.cmds, "savePrefs", create=True),
        ):
            Macros.apply_bindings(
                {
                    "m_bad": {"key": "F5", "cat": "Edit"},  # raises first...
                    "m_group": {"key": "F6", "cat": "Edit"},  # ...rest applies
                    "m_unbind": {"key": "", "cat": ""},
                }
            )
        self.assertEqual(applied, ["m_group"])
        clear.assert_called_once_with("m_unbind")


class TestApplyBindingsIdempotent(QuickTestCase):
    """apply_bindings must be a no-op — and must NOT flush prefs — when the live
    hotkey registry already matches the target preset. The launch-time re-apply
    (``apply_saved_macros`` from TclMaya) otherwise recreated every
    runtimeCommand and forced ``savePrefs(hotkeys=True)`` on every start, so the
    Script Editor logged "Saving runtime commands / Saving hotkeys / Saving
    named commands" each launch even though nothing had changed."""

    def test_matching_preset_applies_nothing_and_skips_saveprefs(self):
        from unittest import mock
        from mayatk.edit_utils import macros

        # Live registry already equals the preset (one bound, one unbound).
        live = {
            "m_wireframe": {"key": "ctl+i", "cat": "Display"},
            "m_group": {"key": "", "cat": "Edit"},
        }
        with (
            mock.patch.object(Macros, "get_current_bindings", return_value=live),
            mock.patch.object(
                Macros,
                "_key_bound_to",
                return_value=True,  # target chord already live
            ),
            mock.patch.object(
                # A converged macro has its runTimeCommand too — the no-op check
                # requires both (a bound chord whose command is gone must re-apply).
                macros.cmds,
                "runTimeCommand",
                return_value=True,
                create=True,
            ),
            mock.patch.object(Macros, "set_macro") as set_macro,
            mock.patch.object(Macros, "clear_hotkey") as clear,
            mock.patch.object(macros.cmds, "savePrefs", create=True) as save,
        ):
            Macros.apply_bindings(
                {
                    "m_wireframe": {"key": "ctl+i", "cat": "Display"},
                    "m_group": {"key": "", "cat": "Edit"},  # already unbound
                }
            )
        set_macro.assert_not_called()
        clear.assert_not_called()
        save.assert_not_called()  # nothing changed -> no prefs flush

    def test_multibound_target_present_is_noop(self):
        """THE recurrence bug: a command bound to several chords (sht+q + extras
        added in Maya's Hotkey Editor) collapses to ONE, non-deterministically-
        chosen entry in live_hotkey_map. When that entry is an EXTRA (here
        sht+g) rather than the preset key (sht+q), the old normalize-compare
        re-applied + flushed prefs — on some launches only, which is why the
        save spam seemed random. The target key IS live, so it must be a no-op."""
        from unittest import mock
        from mayatk.edit_utils import macros

        # live_hotkey_map returned an extra chord, not the preset's sht+q...
        live = {"m_object_selection": {"key": "sht+g", "cat": "Edit"}}
        with (
            mock.patch.object(Macros, "get_current_bindings", return_value=live),
            mock.patch.object(
                Macros,
                "_key_bound_to",
                return_value=True,  # ...but sht+q IS bound
            ) as kb,
            mock.patch.object(
                # Converged state includes the runTimeCommand (see the sibling test).
                macros.cmds,
                "runTimeCommand",
                return_value=True,
                create=True,
            ),
            mock.patch.object(Macros, "set_macro") as set_macro,
            mock.patch.object(Macros, "clear_hotkey") as clear,
            mock.patch.object(macros.cmds, "savePrefs", create=True) as save,
        ):
            Macros.apply_bindings(
                {"m_object_selection": {"key": "sht+q", "cat": "Edit"}}
            )
        kb.assert_called_once_with("m_object_selection", "sht+q")
        set_macro.assert_not_called()
        clear.assert_not_called()
        save.assert_not_called()  # target already live -> no flush

    def test_real_difference_applies_and_flushes_once(self):
        from unittest import mock
        from mayatk.edit_utils import macros

        live = {"m_wireframe": {"key": "F1", "cat": "Display"}}
        with (
            mock.patch.object(Macros, "get_current_bindings", return_value=live),
            mock.patch.object(
                Macros,
                "_key_bound_to",
                return_value=False,  # target F2 not yet live
            ),
            mock.patch.object(Macros, "set_macro") as set_macro,
            mock.patch.object(Macros, "clear_hotkey") as clear,
            mock.patch.object(macros.cmds, "savePrefs", create=True) as save,
        ):
            Macros.apply_bindings({"m_wireframe": {"key": "F2", "cat": "Display"}})
        # Rebinding must release the stale live chord first: set_macro only
        # ADDS a binding, so without the clear the command stays multi-bound,
        # the registry keeps reporting the old chord, and the launch diff
        # re-applies (and re-saves prefs) forever.
        clear.assert_called_once_with("m_wireframe", key="F1")
        set_macro.assert_called_once_with("m_wireframe", key="F2", cat="Display")
        save.assert_called_once()  # a genuine change still persists

    def test_unbound_macro_binds_without_clearing(self):
        from unittest import mock
        from mayatk.edit_utils import macros

        # No live key -> nothing to release; just bind.
        live = {"m_wireframe": {"key": "", "cat": "Display"}}
        with (
            mock.patch.object(Macros, "get_current_bindings", return_value=live),
            mock.patch.object(Macros, "_key_bound_to", return_value=False),
            mock.patch.object(Macros, "set_macro") as set_macro,
            mock.patch.object(Macros, "clear_hotkey") as clear,
            mock.patch.object(macros.cmds, "savePrefs", create=True),
        ):
            Macros.apply_bindings({"m_wireframe": {"key": "F2", "cat": "Display"}})
        clear.assert_not_called()
        set_macro.assert_called_once_with("m_wireframe", key="F2", cat="Display")

    def test_chord_swap_does_not_clobber_sibling_target(self):
        from unittest import mock
        from mayatk.edit_utils import macros

        # m_wireframe and m_group swap chords in one apply. Neither stale chord
        # may be cleared — each is the OTHER's target, and set_macro (last write
        # wins on a key) reassigns it. Clearing would wipe the sibling's set.
        live = {
            "m_wireframe": {"key": "F1", "cat": "Display"},
            "m_group": {"key": "F2", "cat": "Edit"},
        }
        with (
            mock.patch.object(Macros, "get_current_bindings", return_value=live),
            mock.patch.object(
                Macros,
                "_key_bound_to",
                return_value=False,  # neither target live yet
            ),
            mock.patch.object(Macros, "set_macro") as set_macro,
            mock.patch.object(Macros, "clear_hotkey") as clear,
            mock.patch.object(macros.cmds, "savePrefs", create=True),
        ):
            Macros.apply_bindings(
                {
                    "m_wireframe": {"key": "F2", "cat": "Display"},  # was F1
                    "m_group": {"key": "F1", "cat": "Edit"},  # was F2
                }
            )
        clear.assert_not_called()  # both stale chords are targets -> no clear
        self.assertEqual(set_macro.call_count, 2)


class TestApplySavedMacros(_NoPrefsFlush, _TempPresetRoot, MayaTkTestCase):
    """Startup path: apply_saved_macros() resolves active -> shipped default."""

    NAMES = ("m_wireframe", "m_invert_selection", "m_group")

    def tearDown(self):
        for name in self.NAMES:
            try:
                if cmds.runTimeCommand(name, exists=True) and not cmds.runTimeCommand(
                    name, query=True, default=True
                ):
                    cmds.runTimeCommand(name, edit=True, delete=True)
            except Exception:
                pass
        super().tearDown()

    def test_default_registers_nothing_when_no_active_preset(self):
        # The shipped 'default' is all-unbound, so with no active preset the
        # startup path must not register any macro runTimeCommands.
        Macros.apply_saved_macros()
        for name in self.NAMES:
            self.assertFalse(
                cmds.runTimeCommand(name, exists=True),
                f"{name} unexpectedly registered by the empty default preset",
            )

    def test_applies_active_user_preset(self):
        bindings = {
            "m_wireframe": {"key": "3", "cat": "Display"},
            "m_group": {"key": "ctl+g", "cat": "Edit"},
        }
        Macros.save_preset("unittest_active", bindings)  # also sets it active
        try:
            Macros.apply_saved_macros()
            for name in bindings:
                self.assertTrue(
                    cmds.runTimeCommand(name, exists=True),
                    f"{name} not registered from the active user preset",
                )
        finally:
            Macros.delete_preset("unittest_active")

    def test_apply_rebuilds_missing_runtime_command_when_chord_still_bound(self):
        """Deleting a runTimeCommand does NOT release its chord (see
        set_macro's multi-bound note), leaving a binding that ERRORS on
        keypress — the "hotkey set but broken" state. apply_bindings' no-op
        check must not treat that stale chord as "already applied": it must
        re-register the command so the startup applier heals the key.
        (Regression: the check tested only the chord, so a session/prefs
        state with the binding but no command was skipped forever.)"""
        name, key = "m_wireframe", "3"
        Macros.set_macro(name, key=key, cat="Display")
        if not Macros._key_bound_to(name, key):
            self.skipTest("live hotkey registry unavailable (headless)")
        cmds.runTimeCommand(name, edit=True, delete=True)  # chord survives
        self.assertTrue(
            Macros._key_bound_to(name, key),
            "precondition: the chord must survive the command's deletion",
        )
        Macros.apply_bindings({name: {"key": key, "cat": "Display"}})
        self.assertTrue(
            cmds.runTimeCommand(name, exists=True),
            "apply_bindings must re-register a macro whose chord is bound "
            "but whose runTimeCommand is missing",
        )


class TestEditorGlue(QuickTestCase):
    """Provider callables behind ``Macros.show_editor`` — the data the unified
    uitk ShortcutEditor renders and the routes its edits/presets take. Live
    state is patched so these are pure logic tests (no hotkey mutation)."""

    # m_grid: bound; m_group: unbound at its default category;
    # m_wireframe: unbound but re-categorised (a preset-carried override).
    _LIVE = {
        "m_grid": {"key": "ctl+g", "cat": "Display"},
        "m_group": {"key": "", "cat": "Edit"},
        "m_wireframe": {"key": "", "cat": "Custom"},
    }

    def _patched_live(self):
        live = {
            name: dict(self._LIVE.get(name) or {"key": "", "cat": ""})
            for name in Macros.list_available_macros()
        }
        for name, spec in live.items():
            spec["cat"] = spec.get("cat") or Macros.macro_category(name)
        return live

    def test_registry_entries_are_editor_shaped_and_category_filtered(self):
        with (
            patch.object(
                Macros, "get_current_bindings", side_effect=self._patched_live
            ),
            patch.object(Macros, "_default_bindings", return_value={}),
        ):
            entries = {e["method"]: e for e in Macros.get_editor_registry("Display")}
        self.assertIn("m_grid", entries)
        self.assertNotIn("m_group", entries)  # Edit category
        self.assertNotIn("m_wireframe", entries)  # re-categorised away
        entry = entries["m_grid"]
        self.assertEqual(entry["name"], "Grid")
        self.assertEqual(entry["current"], "Ctrl+G")  # converted to Qt form
        self.assertEqual(entry["current_scope"], "application")
        self.assertFalse(entry["scope_editable"])  # native keys are DCC-global
        for field in (
            "method",
            "name",
            "doc",
            "current",
            "default",
            "current_scope",
            "default_scope",
        ):
            self.assertIn(field, entry)

    def test_custom_category_becomes_a_group(self):
        with (
            patch.object(
                Macros, "get_current_bindings", side_effect=self._patched_live
            ),
            patch.object(Macros, "_default_bindings", return_value={}),
        ):
            cats = Macros.editor_categories()
            customs = [e["method"] for e in Macros.get_editor_registry("Custom")]
        self.assertIn("Custom", cats)
        for mixin_cat in Macros.list_categories():
            self.assertIn(mixin_cat, cats)
        self.assertEqual(customs, ["m_wireframe"])

    def test_export_bindings_captures_bound_and_category_overrides_only(self):
        """Bound macros + category overrides are saved; unbound macros at their
        mixin-default category are omitted (regression carried over from the
        retired panel: only-bound exports dropped re-categorisations)."""
        with patch.object(
            Macros, "get_current_bindings", side_effect=self._patched_live
        ):
            out = Macros.export_bindings()
        self.assertEqual(out["m_grid"], {"key": "ctl+g", "cat": "Display"})
        self.assertEqual(out["m_wireframe"], {"key": "", "cat": "Custom"})
        self.assertNotIn("m_group", out)  # unbound at default category

    def test_import_bindings_releases_uncovered_keys_then_applies(self):
        cleared, applied = [], []
        with (
            patch.object(
                Macros, "get_current_bindings", side_effect=self._patched_live
            ),
            patch.object(
                Macros,
                "clear_hotkey",
                side_effect=lambda n, key=None: cleared.append((n, key)),
            ),
            patch.object(
                Macros, "apply_bindings", side_effect=lambda d: applied.append(d)
            ),
        ):
            count = Macros.import_bindings({"m_group": {"key": "g", "cat": "Edit"}})
        self.assertEqual(count, 1)
        self.assertIn(("m_grid", "ctl+g"), cleared)  # live key not in the set
        self.assertEqual(applied, [{"m_group": {"key": "g", "cat": "Edit"}}])

    def test_apply_editor_binding_rebinds_clears_and_sets(self):
        calls = []
        with (
            patch.object(
                Macros, "get_current_bindings", side_effect=self._patched_live
            ),
            patch.object(
                Macros,
                "clear_hotkey",
                side_effect=lambda n, key=None: calls.append(("clear", n, key)),
            ),
            patch.object(
                Macros,
                "set_macro",
                side_effect=lambda n, key=None, cat=None: calls.append(
                    ("set", n, key, cat)
                ),
            ),
        ):
            Macros.apply_editor_binding("m_grid", "Ctrl+J")  # rebind
            Macros.apply_editor_binding("m_grid", "")  # clear
            Macros.apply_editor_binding("m_group", "Alt+G")  # fresh assign
        self.assertEqual(
            calls,
            [
                ("clear", "m_grid", "ctl+g"),
                ("set", "m_grid", "ctl+j", "Display"),
                ("clear", "m_grid", "ctl+g"),
                ("set", "m_group", "alt+g", "Edit"),
            ],
        )

    def test_apply_editor_binding_same_key_is_not_cleared(self):
        calls = []
        with (
            patch.object(
                Macros, "get_current_bindings", side_effect=self._patched_live
            ),
            patch.object(
                Macros,
                "clear_hotkey",
                side_effect=lambda n, key=None: calls.append(("clear", n, key)),
            ),
            patch.object(
                Macros,
                "set_macro",
                side_effect=lambda n, key=None, cat=None: calls.append(
                    ("set", n, key, cat)
                ),
            ),
        ):
            Macros.apply_editor_binding("m_grid", "Ctrl+G")  # unchanged chord
        self.assertNotIn(("clear", "m_grid", "ctl+g"), calls)


class _FakeClock:
    """Injected StepToggle clock — advance ``now`` instead of sleeping."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class TestFrameMacro(MayaTkTestCase):
    """m_frame's step cycle — the framing itself is viewport work (GUI-only), so the
    headless half asserts the selection profiling and the step/restore bookkeeping."""

    def setUp(self):
        super().setUp()
        import pythontk as ptk

        ptk.StepToggle.clear("mtk_m_frame")

    def tearDown(self):
        import pythontk as ptk

        ptk.StepToggle.clear("mtk_m_frame")
        super().tearDown()

    def test_element_type_scene_with_empty_selection(self):
        self.assertEqual(DisplayMacros._frame_element_type([]), "scene")

    def test_element_type_object_in_object_mode(self):
        cube = cmds.polyCube(name="frame_cube")[0]
        cmds.select(cube)
        cmds.selectMode(object=True)
        self.assertEqual(DisplayMacros._frame_element_type([cube]), "object")

    def test_every_element_type_has_a_fit_factor(self):
        for element_type in ("vertex", "edge", "facet", "object", "scene"):
            self.assertIn(element_type, DisplayMacros.FRAME_FIT_FACTORS)

    def test_fit_factors_frame_like_maya_does(self):
        """Fit factors are *fill* fractions (1.0 fills the view, F is ~0.95): a
        small one is a camera far away, which is what made component framing
        land hundreds of units out. Every first press must be F-like."""
        for element_type, fill in DisplayMacros.FRAME_FIT_FACTORS.items():
            self.assertGreaterEqual(fill, 0.75, element_type)
            self.assertLessEqual(fill, 1.0, element_type)

    def test_step_cycle_frames_steps_in_then_restores_the_view(self):
        """Two steps in, then home — and the restore uses the view snapshotted on entry."""
        cube = cmds.polyCube(name="frame_cycle_cube")[0]
        cmds.select(cube)
        fits, zooms, restored = [], [], []
        state = {"view": "original"}

        with (
            patch("mayatk.edit_utils.macros.cmds.viewFit", side_effect=lambda **kw: fits.append(kw)),
            patch("mayatk.edit_utils.macros.CamUtils.zoom_view", side_effect=lambda **kw: zooms.append(kw)),
            patch("mayatk.edit_utils.macros.CamUtils.get_view_state", side_effect=lambda *a, **k: dict(state)),
            patch("mayatk.edit_utils.macros.CamUtils.set_view_state", side_effect=restored.append),
            patch("mayatk.edit_utils.macros.CamUtils.fit_camera_clipping"),
        ):
            DisplayMacros.m_frame()
            DisplayMacros.m_frame()
            DisplayMacros.m_frame()

        self.assertEqual(len(fits), 2)  # third press restores instead of framing
        # The first press IS the ideal frame (no dolly); the second re-frames and
        # steps in from there. viewFit's fill saturates at 1.0, so the step is a
        # dolly about the framed point rather than a bigger fit factor.
        self.assertEqual(fits[0]["fitFactor"], fits[1]["fitFactor"])
        self.assertEqual(len(zooms), 1)
        self.assertGreater(zooms[0]["factor"], 1.0)
        self.assertEqual(restored, [{"view": "original"}])

    def test_single_step_is_frame_then_restore(self):
        cube = cmds.polyCube(name="frame_single_cube")[0]
        cmds.select(cube)
        fits, restored = [], []

        with (
            patch("mayatk.edit_utils.macros.cmds.viewFit", side_effect=lambda **kw: fits.append(kw)),
            patch("mayatk.edit_utils.macros.CamUtils.zoom_view") as zoom,
            patch("mayatk.edit_utils.macros.CamUtils.get_view_state", return_value={"v": 1}),
            patch("mayatk.edit_utils.macros.CamUtils.set_view_state", side_effect=restored.append),
            patch("mayatk.edit_utils.macros.CamUtils.fit_camera_clipping"),
        ):
            DisplayMacros.m_frame(steps=1)
            DisplayMacros.m_frame(steps=1)

        self.assertEqual(len(fits), 1)
        # A single step is the unscaled ideal for the selection kind.
        self.assertAlmostEqual(
            fits[0]["fitFactor"], DisplayMacros.FRAME_FIT_FACTORS["object"]
        )
        zoom.assert_not_called()
        self.assertEqual(restored, [{"v": 1}])

    def test_reselecting_restarts_the_cycle(self):
        cube_a = cmds.polyCube(name="frame_a")[0]
        cube_b = cmds.polyCube(name="frame_b")[0]
        fits = []

        with (
            patch("mayatk.edit_utils.macros.cmds.viewFit", side_effect=lambda **kw: fits.append(kw)),
            patch("mayatk.edit_utils.macros.CamUtils.zoom_view") as zoom,
            patch("mayatk.edit_utils.macros.CamUtils.get_view_state", return_value={}),
            patch("mayatk.edit_utils.macros.CamUtils.set_view_state"),
            patch("mayatk.edit_utils.macros.CamUtils.fit_camera_clipping"),
        ):
            cmds.select(cube_a)
            DisplayMacros.m_frame()
            cmds.select(cube_b)
            DisplayMacros.m_frame()

        self.assertEqual(len(fits), 2)
        self.assertEqual(fits[0]["fitFactor"], fits[1]["fitFactor"])  # both step 1
        zoom.assert_not_called()  # ... and neither one stepped in

    def test_clipping_fit_is_opt_out(self):
        cube = cmds.polyCube(name="frame_clip_cube")[0]
        cmds.select(cube)

        with (
            patch("mayatk.edit_utils.macros.cmds.viewFit"),
            patch("mayatk.edit_utils.macros.CamUtils.zoom_view"),
            patch("mayatk.edit_utils.macros.CamUtils.get_view_state", return_value={}),
            patch("mayatk.edit_utils.macros.CamUtils.set_view_state"),
            patch("mayatk.edit_utils.macros.CamUtils.fit_camera_clipping") as fit,
        ):
            DisplayMacros.m_frame(adjust_clipping=False)
            fit.assert_not_called()
            DisplayMacros.m_frame(adjust_clipping=True)
            fit.assert_called_once()

    def test_stale_retarget_starts_a_new_cycle_with_a_fresh_home(self):
        """After a pause, framing something else is a new session — its home must be
        the view being left now, not the one from the cycle the user abandoned."""
        import pythontk as ptk

        clock = _FakeClock()
        ptk.StepToggle.clear("mtk_m_frame")
        ptk.StepToggle.get("mtk_m_frame", clock=clock)  # the macro reuses this instance

        cube_a = cmds.polyCube(name="stale_a")[0]
        cube_b = cmds.polyCube(name="stale_b")[0]
        views, restored = ["view_A", "view_B"], []

        with (
            patch("mayatk.edit_utils.macros.cmds.viewFit"),
            patch("mayatk.edit_utils.macros.CamUtils.zoom_view"),
            patch("mayatk.edit_utils.macros.CamUtils.get_view_state", side_effect=lambda *a, **k: views.pop(0)),
            patch("mayatk.edit_utils.macros.CamUtils.set_view_state", side_effect=restored.append),
            patch("mayatk.edit_utils.macros.CamUtils.fit_camera_clipping"),
        ):
            cmds.select(cube_a)
            DisplayMacros.m_frame()  # snapshots view_A
            clock.now += 10.0  # ... the user works for a while
            cmds.select(cube_b)
            DisplayMacros.m_frame()  # stale retarget -> new cycle, snapshots view_B
            DisplayMacros.m_frame()  # step 2
            DisplayMacros.m_frame()  # home

        self.assertEqual(restored, ["view_B"])

    def test_stale_press_on_the_same_selection_frames_again(self):
        """Bound to F, the key must behave like F: after the user has tumbled and
        worked for a while, pressing it re-frames the selection. It must NOT
        restore the view from before the first frame — that teleported the
        user to a stale view whose centre of interest was nowhere near the
        selection, so tumbling then orbited nothing they were looking at."""
        import pythontk as ptk

        clock = _FakeClock()
        ptk.StepToggle.clear("mtk_m_frame")
        ptk.StepToggle.get("mtk_m_frame", clock=clock)

        cube = cmds.polyCube(name="stale_same")[0]
        cmds.select(cube)
        fits, restored, views = [], [], ["view_A", "view_B"]

        with (
            patch("mayatk.edit_utils.macros.cmds.viewFit", side_effect=lambda **kw: fits.append(kw)),
            patch("mayatk.edit_utils.macros.CamUtils.zoom_view"),
            patch("mayatk.edit_utils.macros.CamUtils.get_view_state", side_effect=lambda *a, **k: views.pop(0)),
            patch("mayatk.edit_utils.macros.CamUtils.set_view_state", side_effect=restored.append),
            patch("mayatk.edit_utils.macros.CamUtils.fit_camera_clipping"),
        ):
            DisplayMacros.m_frame()  # frame
            clock.now += 10.0  # ... tumble around, work ...
            DisplayMacros.m_frame()  # frame again — NOT "back"
            self.assertEqual(restored, [])
            self.assertEqual(len(fits), 2)
            # A rapid cycle from here unwinds to the view being left NOW.
            DisplayMacros.m_frame()  # step 2
            DisplayMacros.m_frame()  # home
        self.assertEqual(restored, ["view_B"])

    def test_sizeless_selection_is_framed_relative_to_its_object(self):
        """Maya frames a single vertex at a fixed ~1 unit whatever the scale. On
        a large object that is a wall; on a tiny one it is ten times the part.
        The correction pulls the framed region into FRAME_POINT_CONTEXT of the
        owner's diagonal — and leaves anything with a size (F-like) alone."""
        low, high = DisplayMacros.FRAME_POINT_CONTEXT
        unit = DisplayMacros.FRAME_POINT_SIZE

        big = cmds.polyCube(w=200, h=200, d=200, name="point_big")[0]
        diagonal = 200 * 3**0.5
        factor = DisplayMacros._frame_point_correction([f"{big}.vtx[0]"])
        self.assertLess(factor, 1.0)  # back out: see more than 1 unit of it
        self.assertAlmostEqual(factor, unit / (low * diagonal), places=6)

        small = cmds.polyCube(w=0.2, h=0.2, d=0.2, name="point_small")[0]
        diagonal = 0.2 * 3**0.5
        factor = DisplayMacros._frame_point_correction([f"{small}.vtx[0]"])
        self.assertGreater(factor, 1.0)  # move in: the part fills the view
        self.assertAlmostEqual(factor, unit / (high * diagonal), places=6)

        mid = cmds.polyCube(w=2, h=2, d=2, name="point_mid")[0]
        self.assertEqual(DisplayMacros._frame_point_correction([f"{mid}.vtx[0]"]), 1.0)

        # Anything with a size is framed exactly as F would frame it.
        self.assertEqual(DisplayMacros._frame_point_correction([f"{big}.vtx[0:3]"]), 1.0)
        self.assertEqual(DisplayMacros._frame_point_correction([f"{big}.f[0]"]), 1.0)
        self.assertEqual(DisplayMacros._frame_point_correction([big]), 1.0)

    def test_sizeless_correction_survives_a_non_dag_selection(self):
        self.assertEqual(DisplayMacros._frame_point_correction(["lambert1"]), 1.0)

    def test_sizeless_selection_steps_in_from_its_corrected_frame(self):
        big = cmds.polyCube(w=200, h=200, d=200, name="point_step")[0]
        cmds.select(f"{big}.vtx[0]")
        cmds.selectMode(component=True)
        cmds.selectType(vertex=True)
        zooms = []
        try:
            with (
                patch("mayatk.edit_utils.macros.cmds.viewFit"),
                patch("mayatk.edit_utils.macros.CamUtils.zoom_view", side_effect=lambda **kw: zooms.append(kw["factor"])),
                patch("mayatk.edit_utils.macros.CamUtils.get_view_state", return_value={}),
                patch("mayatk.edit_utils.macros.CamUtils.set_view_state"),
                patch("mayatk.edit_utils.macros.CamUtils.fit_camera_clipping"),
            ):
                DisplayMacros.m_frame()
                DisplayMacros.m_frame()
        finally:
            cmds.selectMode(object=True)
        correction = DisplayMacros._frame_point_correction([f"{big}.vtx[0]"])
        self.assertEqual(len(zooms), 2)
        self.assertAlmostEqual(zooms[0], correction)  # first press: corrected frame
        self.assertGreater(zooms[1], zooms[0])  # second press: steps in from it

    def test_empty_selection_frames_all_objects(self):
        cmds.select(clear=True)
        fits = []

        with (
            patch("mayatk.edit_utils.macros.cmds.viewFit", side_effect=lambda **kw: fits.append(kw)),
            patch("mayatk.edit_utils.macros.CamUtils.zoom_view"),
            patch("mayatk.edit_utils.macros.CamUtils.get_view_state", return_value={}),
            patch("mayatk.edit_utils.macros.CamUtils.set_view_state"),
            patch("mayatk.edit_utils.macros.CamUtils.fit_camera_clipping"),
        ):
            DisplayMacros.m_frame()

        self.assertEqual(len(fits), 1)
        self.assertTrue(fits[0].get("allObjects"))


if __name__ == "__main__":
    unittest.main()
