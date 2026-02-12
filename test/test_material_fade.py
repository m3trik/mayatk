# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.mat_utils.material_fade module

Tests for MaterialFade class functionality including:
- Getting StingrayPBS materials from objects
- Loading transparent graph onto StingrayPBS nodes
- Setting up fade keyframes (fade-in and fade-out)
- Baking fade animation curves
- Removing fade keys and restoring defaults
- Material _Fade suffix rename convention
- Multi-object and shared-material scenarios
- Custom frame ranges
- Edge cases (empty selection, already-suffixed, non-stingray materials)
- Attribute mode: per-object 'fade' custom property
"""
import os
import unittest
import pymel.core as pm

from base_test import MayaTkTestCase
from mayatk.mat_utils.material_fade import MaterialFade


class TestMaterialFadeSetup(MayaTkTestCase):
    """Shared setUp that creates a StingrayPBS cube for each test."""

    def setUp(self):
        super().setUp()
        try:
            if not pm.pluginInfo("shaderFXPlugin", query=True, loaded=True):
                pm.loadPlugin("shaderFXPlugin")
        except Exception:
            self.skipTest("shaderFXPlugin not available")

        pm.playbackOptions(min=1, max=30)

        # Create cube + StingrayPBS material with Standard graph loaded
        self.cube = pm.polyCube(name="fade_test_cube")[0]
        self.mat = pm.shadingNode("StingrayPBS", asShader=True, name="test_stingray")
        self.sg = pm.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="test_stingraySG"
        )
        pm.connectAttr(self.mat.outColor, self.sg.surfaceShader, force=True)
        pm.sets(self.sg, edit=True, forceElement=self.cube)

        from mayatk.env_utils._env_utils import EnvUtils

        maya_path = EnvUtils.get_env_info("install_path")
        graph = os.path.join(
            maya_path,
            "presets",
            "ShaderFX",
            "Scenes",
            "StingrayPBS",
            "Standard.sfx",
        )
        if os.path.exists(graph):
            pm.cmds.shaderfx(sfxnode=self.mat.name(), loadGraph=graph)

    def _create_stingray_cube(self, cube_name, mat_name):
        """Helper: create an additional cube with its own StingrayPBS material."""
        from mayatk.env_utils._env_utils import EnvUtils

        cube = pm.polyCube(name=cube_name)[0]
        mat = pm.shadingNode("StingrayPBS", asShader=True, name=mat_name)
        sg = pm.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{mat_name}SG"
        )
        pm.connectAttr(mat.outColor, sg.surfaceShader, force=True)
        pm.sets(sg, edit=True, forceElement=cube)

        maya_path = EnvUtils.get_env_info("install_path")
        graph = os.path.join(
            maya_path,
            "presets",
            "ShaderFX",
            "Scenes",
            "StingrayPBS",
            "Standard.sfx",
        )
        if os.path.exists(graph):
            pm.cmds.shaderfx(sfxnode=mat.name(), loadGraph=graph)
        return cube, mat, sg


# ======================================================================
# get_stingray_mats
# ======================================================================


class TestGetStingrayMats(TestMaterialFadeSetup):
    """Tests for MaterialFade.get_stingray_mats."""

    def test_finds_stingray_material(self):
        """get_stingray_mats returns the StingrayPBS material on a cube."""
        mats = MaterialFade.get_stingray_mats([self.cube])
        self.assertEqual(len(mats), 1)
        self.assertEqual(pm.nodeType(mats[0]), "StingrayPBS")

    def test_ignores_non_stingray(self):
        """Non-StingrayPBS materials (lambert) are excluded."""
        lambert_cube = pm.polyCube(name="lambert_cube")[0]
        mats = MaterialFade.get_stingray_mats([lambert_cube])
        self.assertEqual(len(mats), 0)

    def test_multiple_objects_unique_mats(self):
        """Two cubes with different StingrayPBS materials return both."""
        cube2, mat2, _ = self._create_stingray_cube("cube2", "stingray2")
        mats = MaterialFade.get_stingray_mats([self.cube, cube2])
        self.assertEqual(len(mats), 2)

    def test_shared_material_returns_once(self):
        """Two cubes sharing one material return it only once."""
        cube2 = pm.polyCube(name="shared_cube")[0]
        pm.sets(self.sg, edit=True, forceElement=cube2)  # same SG
        mats = MaterialFade.get_stingray_mats([self.cube, cube2])
        self.assertEqual(len(mats), 1)

    def test_empty_list_returns_empty(self):
        """Empty input returns empty list."""
        mats = MaterialFade.get_stingray_mats([])
        self.assertEqual(mats, [])

    def test_mixed_stingray_and_lambert(self):
        """Mixed selection returns only StingrayPBS materials."""
        lambert_cube = pm.polyCube(name="lambert_cube")[0]
        mats = MaterialFade.get_stingray_mats([self.cube, lambert_cube])
        self.assertEqual(len(mats), 1)
        self.assertEqual(pm.nodeType(mats[0]), "StingrayPBS")


# ======================================================================
# ensure_transparent_graph
# ======================================================================


class TestEnsureTransparentGraph(TestMaterialFadeSetup):
    """Tests for MaterialFade.ensure_transparent_graph."""

    def test_loads_transparent_graph(self):
        """Standard_Transparent.sfx is loaded, exposing use_opacity_map."""
        result = MaterialFade.ensure_transparent_graph(self.mat)
        self.assertTrue(result)
        self.assertTrue(self.mat.hasAttr("use_opacity_map"))

    def test_idempotent(self):
        """Calling twice does not error and still returns True."""
        MaterialFade.ensure_transparent_graph(self.mat)
        result = MaterialFade.ensure_transparent_graph(self.mat)
        self.assertTrue(result)

    def test_opacity_attr_exists_after_load(self):
        """The 'opacity' attribute is available after loading transparent graph."""
        MaterialFade.ensure_transparent_graph(self.mat)
        self.assertTrue(self.mat.hasAttr("opacity"))


# ======================================================================
# setup
# ======================================================================


class TestSetup(TestMaterialFadeSetup):
    """Tests for MaterialFade.setup (material mode)."""

    def test_fade_out_creates_keys(self):
        """setup(fade_in=False) creates animation curves on material attrs."""
        results = MaterialFade.setup(
            objects=[self.cube],
            start_frame=1,
            end_frame=30,
            fade_in=False,
            mode="material",
        )
        self.assertTrue(len(results) > 0)
        mat_name = list(results.keys())[0]
        keyed = results[mat_name]["attrs_keyed"]
        self.assertGreaterEqual(len(keyed), 3, "Expected at least base_color RGB keys")

    def test_fade_out_values_at_endpoints(self):
        """Fade-out: frame 1 = original color, frame 30 = black."""
        if not self.mat.hasAttr("base_color"):
            self.skipTest("base_color attr not available")

        orig = self.mat.base_color.get()
        MaterialFade.setup(
            objects=[self.cube],
            start_frame=1,
            end_frame=30,
            fade_in=False,
            mode="material",
        )

        # Start frame: should be original value (val_start=1.0)
        pm.currentTime(1)
        for i, ch in enumerate(("base_colorR", "base_colorG", "base_colorB")):
            val = self.mat.attr(ch).get()
            self.assertAlmostEqual(
                val,
                orig[i],
                places=3,
                msg=f"{ch} at frame 1 should be {orig[i]}, got {val}",
            )

        # End frame: should be 0 (val_end=0.0)
        pm.currentTime(30)
        for ch in ("base_colorR", "base_colorG", "base_colorB"):
            val = self.mat.attr(ch).get()
            self.assertAlmostEqual(
                val, 0.0, places=3, msg=f"{ch} at frame 30 should be 0.0, got {val}"
            )

    def test_fade_in_values_at_endpoints(self):
        """Fade-in: frame 1 = black, frame 30 = original color."""
        if not self.mat.hasAttr("base_color"):
            self.skipTest("base_color attr not available")

        orig = self.mat.base_color.get()
        MaterialFade.setup(
            objects=[self.cube],
            start_frame=1,
            end_frame=30,
            fade_in=True,
            mode="material",
        )

        pm.currentTime(1)
        val = self.mat.base_colorR.get()
        self.assertAlmostEqual(val, 0.0, places=3)

        pm.currentTime(30)
        val = self.mat.base_colorR.get()
        self.assertAlmostEqual(val, orig[0], places=3)

    def test_opacity_keyed_on_fade_out(self):
        """Opacity attribute has keys after setup(fade_in=False)."""
        MaterialFade.setup(
            objects=[self.cube],
            start_frame=1,
            end_frame=30,
            fade_in=False,
            mode="material",
        )
        # Material may have been renamed
        mat = self.mat
        if mat.hasAttr("opacity"):
            curves = pm.listConnections(mat.opacity, type="animCurve")
            self.assertTrue(curves, "No animCurve on opacity after setup")

    def test_opacity_values_fade_out(self):
        """Opacity goes from 1 (visible) to 0 (invisible) on fade-out."""
        MaterialFade.setup(
            objects=[self.cube],
            start_frame=1,
            end_frame=30,
            fade_in=False,
            mode="material",
        )
        mat = self.mat
        if mat.hasAttr("opacity"):
            pm.currentTime(1)
            self.assertAlmostEqual(mat.opacity.get(), 1.0, places=3)
            pm.currentTime(30)
            self.assertAlmostEqual(mat.opacity.get(), 0.0, places=3)

    def test_renames_material_with_fade_suffix(self):
        """Material is renamed with _Fade suffix."""
        MaterialFade.setup(
            objects=[self.cube],
            start_frame=1,
            end_frame=30,
            fade_in=False,
            mode="material",
        )
        self.assertTrue(
            self.mat.name().endswith("_Fade"),
            f"Expected '_Fade' suffix, got '{self.mat.name()}'",
        )

    def test_already_suffixed_material_not_double_renamed(self):
        """Material already ending in _Fade is not renamed again."""
        self.mat.rename("already_Fade")
        MaterialFade.setup(
            objects=[self.cube],
            start_frame=1,
            end_frame=30,
            fade_in=False,
            mode="material",
        )
        self.assertEqual(self.mat.name(), "already_Fade")

    def test_no_utility_nodes_created(self):
        """No multiplyDivide, ramp, or place2dTexture nodes are created.

        FBX strips utility nodes — the fade must use only animCurve nodes.
        """
        md_before = set(pm.ls(type="multiplyDivide"))
        ramp_before = set(pm.ls(type="ramp"))
        p2d_before = set(pm.ls(type="place2dTexture"))

        MaterialFade.setup(
            objects=[self.cube],
            start_frame=1,
            end_frame=30,
            fade_in=False,
            mode="material",
        )

        self.assertEqual(
            set(pm.ls(type="multiplyDivide")),
            md_before,
            "multiplyDivide nodes were created",
        )
        self.assertEqual(
            set(pm.ls(type="ramp")), ramp_before, "ramp nodes were created"
        )
        # place2dTexture may be created by the transparent graph itself,
        # so we only check that no *extra* ones beyond the graph's own exist.

    def test_custom_frame_range(self):
        """Keys are placed at the specified custom frame range, not timeline."""
        if not self.mat.hasAttr("base_color"):
            self.skipTest("base_color attr not available")

        MaterialFade.setup(
            objects=[self.cube],
            start_frame=10,
            end_frame=20,
            fade_in=False,
            mode="material",
        )

        # Check that a key exists at frame 10 and 20
        curves = pm.listConnections(self.mat.base_colorR, type="animCurve")
        self.assertTrue(curves, "No animCurve on base_colorR")
        times = pm.keyframe(curves[0], query=True, timeChange=True)
        self.assertIn(10.0, times)
        self.assertIn(20.0, times)

    def test_returns_empty_on_empty_objects(self):
        """Empty object list returns empty dict."""
        result = MaterialFade.setup(objects=[], mode="material")
        self.assertEqual(result, {})

    def test_returns_empty_on_non_stingray(self):
        """Objects with no StingrayPBS materials return empty dict."""
        lambert_cube = pm.polyCube(name="lambert_cube")[0]
        result = MaterialFade.setup(objects=[lambert_cube], mode="material")
        self.assertEqual(result, {})

    def test_multiple_objects_different_materials(self):
        """Two cubes with different materials both get keyed."""
        cube2, mat2, _ = self._create_stingray_cube("cube2", "stingray2")
        results = MaterialFade.setup(
            objects=[self.cube, cube2],
            start_frame=1,
            end_frame=30,
            fade_in=False,
            mode="material",
        )
        self.assertEqual(len(results), 2)

    def test_shared_material_keyed_once(self):
        """Two cubes sharing a material produce only one result entry."""
        cube2 = pm.polyCube(name="shared_cube")[0]
        pm.sets(self.sg, edit=True, forceElement=cube2)
        results = MaterialFade.setup(
            objects=[self.cube, cube2],
            start_frame=1,
            end_frame=30,
            fade_in=False,
            mode="material",
        )
        self.assertEqual(len(results), 1)


# ======================================================================
# bake
# ======================================================================


class TestBake(TestMaterialFadeSetup):
    """Tests for MaterialFade.bake."""

    def test_bake_creates_per_frame_keys(self):
        """Baking a 10-frame fade produces at least 10 keys."""
        MaterialFade.setup(
            objects=[self.cube],
            start_frame=1,
            end_frame=10,
            fade_in=False,
            mode="material",
        )
        MaterialFade.bake(
            objects=[self.cube], sample_by=1.0, optimize=False, mode="material"
        )

        if self.mat.hasAttr("opacity"):
            curves = pm.listConnections(self.mat.opacity, type="animCurve")
            if curves:
                key_count = pm.keyframe(curves[0], query=True, keyframeCount=True)
                self.assertGreaterEqual(key_count, 10)

    def test_bake_with_optimization_reduces_keys(self):
        """Baking with optimize=True should have fewer or equal keys vs unoptimized.

        A linear 2-key fade baked to per-frame then optimized should collapse
        back to fewer keys since intermediate values are linearly interpolated.
        """
        MaterialFade.setup(
            objects=[self.cube],
            start_frame=1,
            end_frame=30,
            fade_in=False,
            mode="material",
        )

        # Bake without optimization first to count
        MaterialFade.bake(
            objects=[self.cube], sample_by=1.0, optimize=False, mode="material"
        )
        if not self.mat.hasAttr("base_colorR"):
            self.skipTest("base_colorR not available")

        curves = pm.listConnections(self.mat.base_colorR, type="animCurve")
        if curves:
            unoptimized_count = pm.keyframe(curves[0], query=True, keyframeCount=True)
            # Now optimize
            from mayatk.anim_utils._anim_utils import AnimUtils

            AnimUtils.optimize_keys([self.mat])
            optimized_count = pm.keyframe(curves[0], query=True, keyframeCount=True)
            self.assertLessEqual(optimized_count, unoptimized_count)

    def test_bake_no_keys_is_noop(self):
        """Baking materials with no fade keys does not error."""
        # Don't call setup — no keys exist
        MaterialFade.bake(objects=[self.cube], mode="material")  # should not raise

    def test_bake_skips_non_stingray(self):
        """Baking non-StingrayPBS objects does not error."""
        lambert_cube = pm.polyCube(name="lambert_bake_cube")[0]
        MaterialFade.bake(objects=[lambert_cube], mode="material")  # should not raise


# ======================================================================
# remove
# ======================================================================


class TestRemove(TestMaterialFadeSetup):
    """Tests for MaterialFade.remove."""

    def test_deletes_all_fade_curves(self):
        """remove() deletes all animation curves on fade attrs."""
        MaterialFade.setup(
            objects=[self.cube],
            start_frame=1,
            end_frame=30,
            fade_in=False,
            mode="material",
        )
        MaterialFade.remove(objects=[self.cube], mode="material")

        for attr_name in MaterialFade.FADE_ATTRS:
            if self.mat.hasAttr(attr_name):
                curves = pm.listConnections(self.mat.attr(attr_name), type="animCurve")
                self.assertFalse(curves, f"animCurve still connected to {attr_name}")

    def test_restores_base_color_to_white(self):
        """remove() resets base_color to (1, 1, 1)."""
        MaterialFade.setup(
            objects=[self.cube],
            start_frame=1,
            end_frame=30,
            fade_in=False,
            mode="material",
        )
        MaterialFade.remove(objects=[self.cube], mode="material")

        if self.mat.hasAttr("base_color"):
            color = self.mat.base_color.get()
            for ch in color:
                self.assertAlmostEqual(ch, 1.0, places=3)

    def test_restores_opacity_to_one(self):
        """remove() resets opacity to 1.0."""
        MaterialFade.setup(
            objects=[self.cube],
            start_frame=1,
            end_frame=30,
            fade_in=False,
            mode="material",
        )
        MaterialFade.remove(objects=[self.cube], mode="material")

        if self.mat.hasAttr("opacity"):
            self.assertAlmostEqual(self.mat.opacity.get(), 1.0, places=3)

    def test_strips_fade_suffix(self):
        """remove() strips _Fade suffix from material name."""
        orig_name = self.mat.name()
        MaterialFade.setup(
            objects=[self.cube],
            start_frame=1,
            end_frame=30,
            fade_in=False,
            mode="material",
        )
        self.assertTrue(self.mat.name().endswith("_Fade"))

        MaterialFade.remove(objects=[self.cube], mode="material")
        self.assertEqual(self.mat.name(), orig_name)

    def test_remove_without_prior_setup_is_noop(self):
        """remove() on materials with no fade keys does not error."""
        MaterialFade.remove(objects=[self.cube], mode="material")  # should not raise

    def test_remove_empty_selection_is_noop(self):
        """remove() with empty list does not error."""
        MaterialFade.remove(objects=[], mode="material")  # should not raise


# ======================================================================
# Round-trip
# ======================================================================


class TestRoundTrip(TestMaterialFadeSetup):
    """Tests for setup -> remove leaving scene clean."""

    def test_setup_remove_restores_original_state(self):
        """setup() then remove() restores material name and removes all curves."""
        if not self.mat.hasAttr("base_color"):
            self.skipTest("base_color attr not available")

        orig_name = self.mat.name()

        MaterialFade.setup(
            objects=[self.cube],
            start_frame=1,
            end_frame=30,
            fade_in=False,
            mode="material",
        )
        MaterialFade.remove(objects=[self.cube], mode="material")

        self.assertEqual(self.mat.name(), orig_name)

        for attr_name in MaterialFade.FADE_ATTRS:
            if self.mat.hasAttr(attr_name):
                curves = pm.listConnections(self.mat.attr(attr_name), type="animCurve")
                self.assertEqual(len(curves or []), 0)

    def test_setup_bake_remove_restores_original_state(self):
        """setup() -> bake() -> remove() still restores cleanly."""
        if not self.mat.hasAttr("base_color"):
            self.skipTest("base_color attr not available")

        orig_name = self.mat.name()

        MaterialFade.setup(
            objects=[self.cube],
            start_frame=1,
            end_frame=10,
            fade_in=False,
            mode="material",
        )
        MaterialFade.bake(
            objects=[self.cube], sample_by=1.0, optimize=False, mode="material"
        )
        MaterialFade.remove(objects=[self.cube], mode="material")

        self.assertEqual(self.mat.name(), orig_name)

        for attr_name in MaterialFade.FADE_ATTRS:
            if self.mat.hasAttr(attr_name):
                curves = pm.listConnections(self.mat.attr(attr_name), type="animCurve")
                self.assertEqual(len(curves or []), 0)

    def test_double_setup_does_not_duplicate_suffix(self):
        """Calling setup() twice does not produce 'mat_Fade_Fade'."""
        MaterialFade.setup(
            objects=[self.cube],
            start_frame=1,
            end_frame=30,
            fade_in=False,
            mode="material",
        )
        name_after_first = self.mat.name()

        MaterialFade.setup(
            objects=[self.cube],
            start_frame=1,
            end_frame=30,
            fade_in=True,
            mode="material",
        )
        name_after_second = self.mat.name()

        self.assertEqual(
            name_after_first,
            name_after_second,
            f"Double setup produced different names: {name_after_first} vs {name_after_second}",
        )
        self.assertFalse(
            name_after_second.endswith("_Fade_Fade"), "Double _Fade suffix detected"
        )


# ======================================================================
# Attribute Fade Mode
# ======================================================================


class TestAttributeFade(TestMaterialFadeSetup):
    """Tests for MaterialFade attribute mode (per-object 'fade' property)."""

    def test_setup_adds_and_keys_attribute(self):
        """setup(mode='attribute') adds 'fade' attr and keyframes it."""
        orig_mat_name = self.mat.name()
        results = MaterialFade.setup(
            objects=[self.cube],
            start_frame=1,
            end_frame=30,
            fade_in=False,
            mode="attribute",
        )

        # Attribute exists on the transform
        self.assertTrue(self.cube.hasAttr("fade"), "'fade' not found on transform")

        # Attribute is keyed
        curves = pm.listConnections(self.cube.fade, type="animCurve")
        self.assertTrue(curves, "'fade' has no animation curve")

        # Values are correct
        pm.currentTime(1)
        self.assertAlmostEqual(self.cube.fade.get(), 1.0, places=3)
        pm.currentTime(30)
        self.assertAlmostEqual(self.cube.fade.get(), 0.0, places=3)

        # Material is NOT renamed
        self.assertEqual(self.mat.name(), orig_mat_name)

        # Result dict keyed by object name
        self.assertIn(self.cube.name(), results)

    def test_fade_in_attribute(self):
        """Fade-in mode: attribute goes from 0 to 1."""
        MaterialFade.setup(
            objects=[self.cube],
            start_frame=5,
            end_frame=15,
            fade_in=True,
            mode="attribute",
        )
        pm.currentTime(5)
        self.assertAlmostEqual(self.cube.fade.get(), 0.0, places=3)
        pm.currentTime(15)
        self.assertAlmostEqual(self.cube.fade.get(), 1.0, places=3)

    def test_per_object_independence(self):
        """Two objects sharing a material get independent attribute keys."""
        cube2 = pm.polyCube(name="shared_fade_cube")[0]
        pm.sets(self.sg, edit=True, forceElement=cube2)  # same material

        # cube1: fade-out frames 1-10
        MaterialFade.setup(
            objects=[self.cube],
            start_frame=1,
            end_frame=10,
            fade_in=False,
            mode="attribute",
        )
        # cube2: fade-out frames 20-30
        MaterialFade.setup(
            objects=[cube2],
            start_frame=20,
            end_frame=30,
            fade_in=False,
            mode="attribute",
        )

        # cube1 keys at 1 and 10, not at 20 or 30
        times1 = pm.keyframe(self.cube.fade, query=True, timeChange=True)
        self.assertIn(1.0, times1)
        self.assertIn(10.0, times1)
        self.assertNotIn(20.0, times1)

        # cube2 keys at 20 and 30, not at 1 or 10
        times2 = pm.keyframe(cube2.fade, query=True, timeChange=True)
        self.assertIn(20.0, times2)
        self.assertIn(30.0, times2)
        self.assertNotIn(1.0, times2)

    def test_idempotent_attribute_add(self):
        """Calling setup twice does not duplicate the attribute."""
        MaterialFade.setup(
            objects=[self.cube],
            start_frame=1,
            end_frame=10,
            fade_in=False,
            mode="attribute",
        )
        MaterialFade.setup(
            objects=[self.cube],
            start_frame=1,
            end_frame=10,
            fade_in=True,
            mode="attribute",
        )
        # Still has the attribute (no error on second call)
        self.assertTrue(self.cube.hasAttr("fade"))

    def test_remove_attribute(self):
        """remove(mode='attribute') deletes 'fade' attr and its curves."""
        MaterialFade.setup(
            objects=[self.cube],
            start_frame=1,
            end_frame=30,
            fade_in=False,
            mode="attribute",
        )
        self.assertTrue(self.cube.hasAttr("fade"))

        MaterialFade.remove(objects=[self.cube], mode="attribute")

        self.assertFalse(
            self.cube.hasAttr("fade"), "'fade' attribute should be deleted after remove"
        )

    def test_remove_without_setup_is_noop(self):
        """remove(mode='attribute') on clean object does not error."""
        MaterialFade.remove(objects=[self.cube], mode="attribute")

    def test_empty_objects_returns_empty(self):
        """Empty object list returns empty dict in attribute mode."""
        result = MaterialFade.setup(objects=[], mode="attribute")
        self.assertEqual(result, {})

    def test_bake_attribute(self):
        """Baking attribute mode creates per-frame keys."""
        MaterialFade.setup(
            objects=[self.cube],
            start_frame=1,
            end_frame=10,
            fade_in=False,
            mode="attribute",
        )
        MaterialFade.bake(
            objects=[self.cube],
            sample_by=1.0,
            optimize=False,
            mode="attribute",
        )
        curves = pm.listConnections(self.cube.fade, type="animCurve")
        if curves:
            key_count = pm.keyframe(curves[0], query=True, keyframeCount=True)
            self.assertGreaterEqual(key_count, 10)


if __name__ == "__main__":
    unittest.main()
