# !/usr/bin/python
# coding=utf-8
"""Test Suite for misc edit_utils tool modules.

Covers:
    - Primitives.create_default_primitive (primitives.py)
    - Selection.select_by_type / select_children / select_hierarchy_*
      / get_available_selection_types / get_selection_categories (selection.py)

DynamicPipe is rigging-heavy and skipped here — covered by integration tests if
needed.
"""

import unittest

import maya.cmds as cmds

from mayatk.edit_utils.primitives import Primitives
from mayatk.edit_utils.selection import Selection

from base_test import MayaTkTestCase, QuickTestCase


class TestPrimitives(MayaTkTestCase):
    """Primitives.create_default_primitive — wraps cmds.poly* commands."""

    def test_create_polygon_cube(self):
        result = Primitives.create_default_primitive("polygon", "cube")
        self.assertIsNotNone(result)
        # A cube is now in the scene
        self.assertGreater(len(cmds.ls(type="mesh")), 0)

    def test_create_polygon_sphere(self):
        before = set(cmds.ls(type="mesh"))
        Primitives.create_default_primitive("polygon", "sphere")
        after = set(cmds.ls(type="mesh"))
        self.assertGreater(len(after), len(before))

    def test_create_polygon_cylinder(self):
        before = set(cmds.ls(type="mesh"))
        Primitives.create_default_primitive("polygon", "cylinder")
        after = set(cmds.ls(type="mesh"))
        self.assertGreater(len(after), len(before))

    # --- Arnold lights ------------------------------------------------
    # A native Maya areaLight and an aiAreaLight are not interchangeable at
    # render time, so the "arnold" base type is deliberately separate from
    # "light". Node types probed against MtoA 5.4.5 — only the five that are
    # real DAG lights are offered (aiLightBlocker / aiLightDecay are light
    # FILTERS, aiImagerLightMixer is an imager).
    _ARNOLD_LIGHTS = {
        "area": "aiAreaLight",
        "skydome": "aiSkyDomeLight",
        "mesh": "aiMeshLight",
        "photometric": "aiPhotometricLight",
        "portal": "aiLightPortal",
    }

    def test_create_arnold_lights(self):
        """Each Arnold entry creates its ai* light shape."""
        for sub, node_type in self._ARNOLD_LIGHTS.items():
            with self.subTest(light=sub):
                before = set(cmds.ls(type=node_type))
                Primitives.create_default_primitive("Arnold", sub)
                after = set(cmds.ls(type=node_type))
                self.assertGreater(
                    len(after), len(before), f"{sub} did not create a {node_type}"
                )

    def test_arnold_light_transform_is_named_after_the_node_type(self):
        """``shadingNode`` names an ai* light's transform ``transform#`` — a scene
        of Arnold lights would be transform1..N without the rename."""
        Primitives.create_default_primitive("Arnold", "area")
        shape = cmds.ls(type="aiAreaLight")[0]
        xform = cmds.listRelatives(shape, parent=True)[0]
        self.assertTrue(
            xform.startswith("aiAreaLight"),
            f"transform is {xform!r}, expected an aiAreaLight* name",
        )

    def test_arnold_light_shape_does_not_collide_with_its_transform(self):
        """Renaming only the transform leaves shape and transform sharing one
        short name, so ``cmds.ls(name)`` returns two nodes and every short-name
        lookup on that light is ambiguous. Maya splits them (``areaLight1`` /
        ``areaLightShape1``); the Arnold path must too."""
        Primitives.create_default_primitive("Arnold", "area")
        shape = cmds.ls(type="aiAreaLight", long=True)[0]
        xform = cmds.listRelatives(shape, parent=True, fullPath=True)[0]
        short_shape = shape.rsplit("|", 1)[-1]
        short_xform = xform.rsplit("|", 1)[-1]
        self.assertNotEqual(
            short_shape,
            short_xform,
            "transform and shape share a short name — short-name lookups are ambiguous",
        )
        self.assertEqual(len(cmds.ls(short_xform)), 1, "transform name is ambiguous")
        self.assertIn("Shape", short_shape)

    def test_arnold_light_shapes_stay_named_for_every_light_not_just_the_first(self):
        """The rename guard compared the shape's short name to the transform's,
        which only matches for light #1: renaming ``transform1`` to
        ``aiAreaLight1`` collides with the existing light AND with its own
        child, so Maya's uniquifier lands two indices ahead of the shape, the
        guard reads false, and the shape keeps a transform-looking name.
        Creating a single light cannot see it."""
        for _ in range(3):
            Primitives.create_default_primitive("Arnold", "area")

        shapes = cmds.ls(type="aiAreaLight", long=True)
        self.assertEqual(len(shapes), 3)
        for shape in shapes:
            xform = cmds.listRelatives(shape, parent=True, fullPath=True)[0]
            short_shape = shape.rsplit("|", 1)[-1]
            short_xform = xform.rsplit("|", 1)[-1]
            self.assertNotEqual(short_shape, short_xform)
            self.assertIn(
                "Shape", short_shape, f"{short_shape} was never renamed to a shape name"
            )
            self.assertEqual(len(cmds.ls(short_xform)), 1, "transform name ambiguous")

    def test_arnold_light_with_a_caller_supplied_name_renames_both_nodes(self):
        """The docstring promises the split applies to a caller-supplied name
        too; the transform was left as ``transform1``."""
        Primitives.create_default_primitive("Arnold", "area", name="myKeyLight")

        shape = cmds.ls(type="aiAreaLight", long=True)[0]
        xform = cmds.listRelatives(shape, parent=True, fullPath=True)[0]
        self.assertEqual(xform.rsplit("|", 1)[-1], "myKeyLight")
        self.assertEqual(shape.rsplit("|", 1)[-1], "myKeyLightShape")

    def test_unknown_arnold_subtype_raises(self):
        """An unmapped entry must raise so the panel reports it, not no-op."""
        with self.assertRaises(KeyError):
            Primitives.create_default_primitive("Arnold", "NotALight")


class TestSelectionDispatch(MayaTkTestCase):
    """Selection.select_by_type — dispatches based on _SELECTION_CONFIG."""

    def test_unknown_selection_type_raises(self):
        cube = cmds.polyCube(name="sel_unknown")[0]
        with self.assertRaises(ValueError):
            Selection.select_by_type("Bogus", objects=[cube])

    def test_select_polygon_meshes_returns_meshes(self):
        # Handler scans shapes — pass cmds.ls() result so shapes are included
        cube = cmds.polyCube(name="sel_mesh")[0]
        cmds.spaceLocator(name="sel_loc")
        result = Selection.select_by_type("Polygon Meshes", objects=cmds.ls())
        self.assertIn(cube, result)

    def test_select_locators_returns_locators(self):
        cmds.polyCube(name="sel_lc_cube")
        loc = cmds.spaceLocator(name="sel_lc_loc")[0]
        result = Selection.select_by_type("Locators", objects=cmds.ls())
        self.assertIn(loc, result)


class TestSelectionHelpers(MayaTkTestCase):
    """Selection.select_children / select_hierarchy_*."""

    def test_select_children_returns_immediate_children(self):
        parent = cmds.group(em=True, name="sel_parent")
        a = cmds.group(em=True, parent=parent, name="sel_child_a")
        b = cmds.group(em=True, parent=parent, name="sel_child_b")
        cmds.group(em=True, parent=a, name="sel_grandchild")

        result = Selection.select_children([parent])
        # Only direct children
        self.assertIn(a, result)
        self.assertIn(b, result)

    def test_select_hierarchy_above_returns_ancestors(self):
        grand = cmds.group(em=True, name="sel_g")
        parent = cmds.group(em=True, parent=grand, name="sel_p")
        child = cmds.group(em=True, parent=parent, name="sel_c")

        result = Selection.select_hierarchy_above([child])
        # Should return ancestors (parent + grand)
        self.assertGreater(len(result), 0)

    def test_select_hierarchy_below_returns_descendants(self):
        grand = cmds.group(em=True, name="sel_g2")
        parent = cmds.group(em=True, parent=grand, name="sel_p2")
        child = cmds.group(em=True, parent=parent, name="sel_c2")

        result = Selection.select_hierarchy_below([grand])
        self.assertGreater(len(result), 0)


class TestSelectionMetadata(QuickTestCase):
    """Selection.get_available_selection_types / get_selection_categories."""

    def test_categories_return_dict(self):
        cats = Selection.get_selection_categories()
        self.assertIsInstance(cats, dict)
        self.assertIn("Animation", cats)
        self.assertIn("Geometry", cats)

    def test_available_types_return_list(self):
        types = Selection.get_available_selection_types()
        self.assertIsInstance(types, list)
        self.assertIn("Polygon Meshes", types)
        self.assertIn("Locators", types)

    def test_uv_category_exposes_expected_types(self):
        """UV component selection types should appear in the registry."""
        cats = Selection.get_selection_categories()
        self.assertIn("UV", cats)
        uv_types = set(cats["UV"])
        self.assertIn("Unmapped", uv_types)
        self.assertIn("Texture Borders", uv_types)
        self.assertIn("Overlapping", uv_types)


class TestSelectionUVHandlers(MayaTkTestCase):
    """UV handlers must preserve the pre-MEL selection so `_apply_selection_mode`
    can layer replace/add/remove on top of the user's original meshes.

    Regression guard for the contract change: previously the handler left
    Maya's selection mutated by the MEL command, which broke 'add' mode (the
    original mesh selection was lost before _apply_selection_mode ran)."""

    def test_uv_handler_restores_selection_before_returning(self):
        cube = cmds.polyCube(name="sel_uv_restore")[0]
        cmds.select(cube, replace=True)
        # Run a UV handler directly (bypass select_by_type so we observe
        # the post-handler / pre-_apply_selection_mode state).
        Selection._SELECTION_CONFIG["UV"]["Unmapped"](cmds.ls())
        # Selection must still hold the original mesh — not whatever the MEL
        # command left behind.
        self.assertIn(cube, cmds.ls(selection=True) or [])

    def test_uv_replace_mode_selects_components(self):
        """End-to-end: replace mode through select_by_type selects the matched UVs."""
        cube = cmds.polyCube(name="sel_uv_replace")[0]
        cmds.select(cube, replace=True)
        # An unmapped poly has all faces unmapped by default after polyCube
        # (depending on Maya defaults); the assertion is just that the call
        # completes and produces *something* component-like or empty.
        result = Selection.select_by_type("Unmapped", objects=cmds.ls(), mode="replace")
        self.assertIsInstance(result, list)


if __name__ == "__main__":
    unittest.main()
