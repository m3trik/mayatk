# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.mat_utils.render_opacity module

Tests for the non-animating AttributeManager-based implementation.
"""
import os
import unittest
import pythontk as ptk
import pymel.core as pm
import maya.cmds as cmds
from mayatk.mat_utils.render_opacity._render_opacity import RenderOpacity
from mayatk.mat_utils.mat_snapshot import MatSnapshot
from base_test import MayaTkTestCase


class TestOpacityAttributeMode(MayaTkTestCase):
    """Tests for mode='attribute' (Game Engine workflow)."""

    def setUp(self):
        super().setUp()
        self.cube = pm.polyCube(name="test_cube")[0]

    def test_create_adds_fade_attribute(self):
        """create(mode='attribute') adds the 'opacity' float attribute."""
        RenderOpacity.create(objects=[self.cube], mode="attribute")

        self.assertTrue(
            self.cube.hasAttr("opacity"), "Attribute 'opacity' was not created"
        )
        attr = self.cube.attr("opacity")
        self.assertEqual(attr.get(), 1.0, "Default value should be 1.0")
        self.assertEqual(attr.getMin(), 0.0, "Min value should be 0.0")
        self.assertEqual(attr.getMax(), 1.0, "Max value should be 1.0")
        self.assertTrue(attr.isKeyable(), "Attribute should be keyable")

    def test_create_does_not_add_keys(self):
        """create(mode='attribute') should NOT add animation keys."""
        RenderOpacity.create(objects=[self.cube], mode="attribute")

        anim = pm.listConnections(self.cube, type="animCurve")
        self.assertFalse(anim, "Attribute mode should not create animation curves")

    def test_remove_deletes_attribute(self):
        """remove(mode='attribute') deletes the attribute."""
        RenderOpacity.create(objects=[self.cube], mode="attribute")
        self.assertTrue(self.cube.hasAttr("opacity"))

        RenderOpacity.remove(objects=[self.cube], mode="attribute")
        self.assertFalse(self.cube.hasAttr("opacity"), "Attribute should be removed")


class TestOpacityMaterialMode(MayaTkTestCase):
    """Tests for mode='material' (StingrayPBS setup)."""

    def setUp(self):
        super().setUp()
        try:
            if not pm.pluginInfo("shaderFXPlugin", query=True, loaded=True):
                pm.loadPlugin("shaderFXPlugin")
        except Exception:
            self.skipTest("shaderFXPlugin not available")

        self.cube = pm.polyCube(name="mat_cube")[0]
        self.mat = pm.shadingNode("StingrayPBS", asShader=True, name="test_stingray")
        self.sg = pm.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="test_sg"
        )
        pm.connectAttr(self.mat.outColor, self.sg.surfaceShader)
        pm.sets(self.sg, forceElement=self.cube)

        # Ensure standard graph is loaded
        from mayatk.env_utils._env_utils import EnvUtils

        graph = os.path.join(
            EnvUtils.get_env_info("install_path"),
            "presets/ShaderFX/Scenes/StingrayPBS/Standard.sfx",
        )
        if os.path.exists(graph):
            pm.cmds.shaderfx(sfxnode=self.mat.name(), loadGraph=graph)

    def test_create_loads_transparent_graph(self):
        """create(mode='material') loads transparent graph."""
        # Baseline: no opacity map check usually, or standard graph

        RenderOpacity.create(objects=[self.cube], mode="material")

        # Check for transparency/opacity attributes exposed by the Transparency graph
        # Standard graph usually has 'use_color_map', but 'use_opacity_map' implies Transparent graph or similar
        self.assertTrue(
            self.mat.hasAttr("use_opacity_map"), "Should have loaded transparent graph"
        )

    def test_create_does_not_add_keys(self):
        """create(mode='material') should NOT add animation keys anymore."""
        RenderOpacity.create(objects=[self.cube], mode="material")

        anim = pm.listConnections(self.mat, type="animCurve")
        self.assertFalse(anim, "Material mode should not create animation curves")

    def test_mode_switching_cleans_previous_mode(self):
        """Switching modes should clean up the previous mode's artifacts.

        Material mode creates an opacity proxy wired to the material.
        Switching to attribute mode should disconnect the proxy.
        Switching back to material mode should reconnect it.
        """
        # 1. Start with Material Mode
        RenderOpacity.create(objects=[self.cube], mode="material")
        self.assertTrue(self.mat.hasAttr("use_opacity_map"))
        self.assertTrue(
            self.cube.hasAttr("opacity"),
            "Material mode should create opacity proxy attr",
        )
        self.assertTrue(
            pm.isConnected(self.cube.opacity, self.mat.opacity),
            "Proxy should drive material opacity",
        )

        # 2. Switch to Attribute Mode — proxy disconnected, attr recreated
        RenderOpacity.create(objects=[self.cube], mode="attribute")
        self.assertTrue(self.cube.hasAttr("opacity"), "Attribute should still exist")
        self.assertFalse(
            pm.isConnected(self.cube.opacity, self.mat.opacity),
            "Material proxy should be disconnected after switching to attribute mode",
        )

        # 3. Switch back to Material Mode — proxy reconnected
        RenderOpacity.create(objects=[self.cube], mode="material")
        self.assertTrue(
            self.cube.hasAttr("opacity"),
            "Material mode should re-create opacity proxy attr",
        )
        mat_after = (
            self.cube.getShape()
            .listConnections(type="shadingEngine")[0]
            .surfaceShader.inputs()[0]
        )
        self.assertTrue(
            pm.isConnected(self.cube.opacity, mat_after.opacity),
            "Proxy should drive material opacity after switching back",
        )

    def test_remove_mode_cleans_all_artifacts(self):
        """mode='remove' removes opacity attr, visibility driver, and proxy."""
        RenderOpacity.create(objects=[self.cube], mode="material")
        self.assertTrue(self.cube.hasAttr("opacity"))

        RenderOpacity.create(objects=[self.cube], mode="remove")

        # Opacity attribute removed
        self.assertFalse(self.cube.hasAttr("opacity"), "opacity attr should be removed")
        # Visibility reset
        self.assertTrue(self.cube.visibility.get(), "Visibility should be True")
        vis_inputs = pm.listConnections(self.cube.visibility, source=True)
        self.assertFalse(vis_inputs, "Visibility should have no driver")
        # Material opacity not driven
        if self.mat.hasAttr("opacity"):
            mat_inputs = self.mat.opacity.inputs(plugs=True)
            self.assertFalse(mat_inputs, "Material opacity should not be driven")

    def test_remove_cleans_fade_duplicate(self):
        """Remove mode reassigns _Fade duplicates back to original material."""
        cube2 = pm.polyCube(name="mat_cube_fade")[0]
        pm.sets(self.sg, forceElement=cube2)

        RenderOpacity.create(objects=[self.cube, cube2], mode="material")

        # Verify cube2 got a _Fade duplicate
        mat2 = (
            cube2.getShape()
            .listConnections(type="shadingEngine")[0]
            .surfaceShader.inputs()[0]
        )
        self.assertIn("_Fade", mat2.name(), "cube2 should have a _Fade material")

        # Remove everything
        RenderOpacity.create(objects=[self.cube, cube2], mode="remove")

        # Both cubes should be back on the original material
        mat_after_1 = (
            self.cube.getShape()
            .listConnections(type="shadingEngine")[0]
            .surfaceShader.inputs()[0]
        )
        mat_after_2 = (
            cube2.getShape()
            .listConnections(type="shadingEngine")[0]
            .surfaceShader.inputs()[0]
        )
        self.assertEqual(
            mat_after_1.name(),
            self.mat.name(),
            "cube1 should be back on original material",
        )
        self.assertEqual(
            mat_after_2.name(),
            self.mat.name(),
            "cube2 should be back on original material",
        )
        self.assertFalse(self.cube.hasAttr("opacity"))
        self.assertFalse(cube2.hasAttr("opacity"))

    def test_material_mode_splits_shared_material(self):
        """Material mode should enforce unique materials for independent fading."""
        # Create a second cube sharing the same material
        cube2 = pm.polyCube(name="mat_cube_2")[0]
        pm.sets(self.sg, forceElement=cube2)

        # Apply to both
        RenderOpacity.create(objects=[self.cube, cube2], mode="material")

        # Verify materials are different
        mat1 = (
            self.cube.getShape()
            .listConnections(type="shadingEngine")[0]
            .surfaceShader.inputs()[0]
        )
        mat2 = (
            cube2.getShape()
            .listConnections(type="shadingEngine")[0]
            .surfaceShader.inputs()[0]
        )

        self.assertNotEqual(mat1, mat2, "Materials should have been split")
        self.assertTrue(pm.isConnected(self.cube.opacity, mat1.opacity))
        self.assertTrue(pm.isConnected(cube2.opacity, mat2.opacity))

    # ------------------------------------------------------------------
    # Texture restoration after graph swap
    # ------------------------------------------------------------------

    def _connect_dummy_textures(self):
        """Wire file nodes to the standard StingrayPBS TEX_* slots.

        Returns a dict mapping logical slot names to the texture paths that
        were assigned so tests can verify they survive the graph swap.
        """
        import tempfile

        expected = {}
        slots = {
            "baseColor": ("TEX_color_map", "outColor"),
            "normal": ("TEX_normal_map", "outColor"),
            "roughness": ("TEX_roughness_map", "outColor"),
            "metallic": ("TEX_metallic_map", "outColor"),
        }
        for logical, (attr_name, out_plug) in slots.items():
            if not self.mat.hasAttr(attr_name):
                continue
            # Create a file node with a synthetic but unique path
            file_node = cmds.shadingNode("file", asTexture=True, isColorManaged=True)
            fake_path = os.path.join(
                tempfile.gettempdir(), f"test_{logical}.png"
            ).replace("\\", "/")
            cmds.setAttr(f"{file_node}.fileTextureName", fake_path, type="string")
            cmds.connectAttr(
                f"{file_node}.{out_plug}", f"{self.mat}.{attr_name}", force=True
            )
            # Enable the toggle so the map is active
            toggle = attr_name.replace("TEX_", "use_", 1)
            if self.mat.hasAttr(toggle):
                self.mat.attr(toggle).set(1.0)
            expected[logical] = fake_path
        return expected

    def _get_connected_texture_path(self, mat, attr_name):
        """Return the fileTextureName of the file node connected to *attr_name*, or None."""
        full = f"{mat}.{attr_name}"
        if not cmds.objExists(full):
            return None
        files = cmds.listConnections(full, source=True, destination=False, type="file")
        if not files:
            return None
        return (cmds.getAttr(f"{files[0]}.fileTextureName") or "").replace("\\", "/")

    def test_textures_restored_after_graph_swap(self):
        """Textures connected before the opacity template swap must survive.

        Bug: loadGraph on StingrayPBS destroys all external connections.
        MatManifest.build / .restore must round-trip them.
        Fixed: 2026-02-13
        """
        expected = self._connect_dummy_textures()
        self.assertTrue(expected, "setUp should have connected at least one texture")

        # Verify connections exist BEFORE the swap
        for logical, (attr_name, _) in {
            "baseColor": ("TEX_color_map", "outColor"),
            "normal": ("TEX_normal_map", "outColor"),
        }.items():
            if logical in expected:
                path = self._get_connected_texture_path(self.mat, attr_name)
                self.assertEqual(path, expected[logical], f"Pre-swap: {logical}")

        # --- Perform the graph swap ---
        RenderOpacity.create(objects=[self.cube], mode="material")

        # The material may have been replaced if it was shared; resolve the
        # actual material assigned after create().
        mat_after = (
            self.cube.getShape()
            .listConnections(type="shadingEngine")[0]
            .surfaceShader.inputs()[0]
        )

        # Verify textures are reconnected on the post-swap material.
        slots = {
            "baseColor": "TEX_color_map",
            "normal": "TEX_normal_map",
            "roughness": "TEX_roughness_map",
            "metallic": "TEX_metallic_map",
        }
        for logical, attr_name in slots.items():
            if logical not in expected:
                continue
            actual = self._get_connected_texture_path(mat_after, attr_name)
            self.assertEqual(
                actual,
                expected[logical],
                f"Texture for '{logical}' ({attr_name}) was not restored after graph swap",
            )

    def test_textures_restored_on_split_duplicate(self):
        """When a shared material is duplicated, the duplicate must also get textures.

        Fixed: 2026-02-13
        """
        expected = self._connect_dummy_textures()
        self.assertTrue(expected, "setUp should have connected at least one texture")

        # Share material with a second cube
        cube2 = pm.polyCube(name="mat_cube_split")[0]
        pm.sets(self.sg, forceElement=cube2)

        RenderOpacity.create(objects=[self.cube, cube2], mode="material")

        # Resolve the actual materials assigned to each object after create.
        for obj in [self.cube, cube2]:
            mat_after = (
                obj.getShape()
                .listConnections(type="shadingEngine")[0]
                .surfaceShader.inputs()[0]
            )
            for logical, attr_name in {
                "baseColor": "TEX_color_map",
                "normal": "TEX_normal_map",
                "roughness": "TEX_roughness_map",
                "metallic": "TEX_metallic_map",
            }.items():
                if logical not in expected:
                    continue
                actual = self._get_connected_texture_path(mat_after, attr_name)
                self.assertEqual(
                    actual,
                    expected[logical],
                    f"[{obj}] Texture for '{logical}' not restored on split duplicate",
                )

    def test_scalar_values_restored_after_graph_swap(self):
        """Scalar material values (e.g. use_color_map toggle) survive the swap.

        Bug: loadGraph resets every attribute to its new graph default.
        MatSnapshot.restore must re-apply captured scalar values.
        Fixed: 2026-02-13
        """
        # Set a known scalar value on the Standard graph
        if self.mat.hasAttr("use_color_map"):
            self.mat.use_color_map.set(0.0)  # Disable color map

        RenderOpacity.create(objects=[self.cube], mode="material")

        mat_after = (
            self.cube.getShape()
            .listConnections(type="shadingEngine")[0]
            .surfaceShader.inputs()[0]
        )

        # The transparent graph also has use_color_map; it should be restored to 0.
        if mat_after.hasAttr("use_color_map"):
            self.assertAlmostEqual(
                mat_after.use_color_map.get(),
                0.0,
                places=4,
                msg="Scalar value 'use_color_map' was not restored after graph swap",
            )


class TestOpacityVisibilityDriver(MayaTkTestCase):
    """Tests for the auto-hide visibility logic."""

    def setUp(self):
        super().setUp()
        self.cube = pm.polyCube(name="vis_cube")[0]

    def test_opacity_drives_visibility(self):
        """Opacity <= 0 should turn off visibility."""
        RenderOpacity.create(objects=[self.cube], mode="attribute")

        self.cube.opacity.set(1.0)
        self.assertEqual(self.cube.visibility.get(), True)

        self.cube.opacity.set(0.0)
        self.assertEqual(self.cube.visibility.get(), False)

        self.cube.opacity.set(0.5)
        self.assertEqual(self.cube.visibility.get(), True)

    def test_remove_restores_visibility(self):
        """Removing fade should remove the visibility driver and reset visibility."""
        RenderOpacity.create(objects=[self.cube], mode="attribute")
        self.cube.opacity.set(0.0)  # Hidden

        RenderOpacity.remove(objects=[self.cube], mode="attribute")

        self.assertTrue(self.cube.visibility.get(), "Visibility should reset to True")
        # Visibility should not be driven (no input connections)
        vis_inputs = pm.listConnections(self.cube.visibility, source=True)
        self.assertFalse(
            vis_inputs,
            "Visibility should not be driven after remove",
        )
        self.assertFalse(
            pm.listConnections(self.cube.visibility, source=True),
            "Visibility should have no input connections",
        )
