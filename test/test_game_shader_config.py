# !/usr/bin/python
# coding=utf-8
import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import pythontk as ptk
import mayatk as mtk
from base_test import MayaTkTestCase
import pymel.core as pm


class GameShaderConfigTest(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.shader = mtk.GameShader()
        self.textures = [
            "model_Base_Color.png",
            "model_Metallic.png",
            "model_Roughness.png",
            "model_Normal.png",
            "model_Ambient_Occlusion.png",
        ]

    def test_config_dict(self):
        """Test passing configuration as a dictionary."""
        config = {
            "shader_type": "standard_surface",
            "create_arnold": True,
            "mask_map": True,
        }

        with patch(
            "pythontk.img_utils.map_factory.MapFactory.prepare_maps"
        ) as mock_prepare:
            # Simulate single mode return
            mock_prepare.return_value = self.textures

            node = self.shader.create_network(
                self.textures, name="test_config_dict", config=config
            )

            # Verify config propagation to prepare_maps
            call_kwargs = mock_prepare.call_args[1]
            self.assertTrue(call_kwargs.get("mask_map"))

            # Check if standard surface was created
            self.assertEqual(mtk.NodeUtils.get_type(node), "shadingEngine")

            surface_shader = pm.listConnections(
                node.surfaceShader, source=True, destination=False
            )
            self.assertTrue(surface_shader)
            self.assertEqual(
                mtk.NodeUtils.get_type(surface_shader[0]), "standardSurface"
            )

            # Check if Arnold nodes were created
            if hasattr(node, "aiSurfaceShader"):
                ai_shader = pm.listConnections(
                    node.aiSurfaceShader, source=True, destination=False
                )
                self.assertTrue(ai_shader)
                self.assertEqual(
                    mtk.NodeUtils.get_type(ai_shader[0]), "aiStandardSurface"
                )

    def test_config_preset(self):
        """Test passing configuration as a preset string."""
        with patch(
            "pythontk.img_utils.map_factory.MapFactory.prepare_maps"
        ) as mock_prepare:
            mock_prepare.return_value = self.textures

            node = self.shader.create_network(
                self.textures, name="test_config_preset", config="Unity HDRP"
            )

            # Verify preset propagation (Unity HDRP has mask_map=True)
            call_kwargs = mock_prepare.call_args[1]
            self.assertTrue(call_kwargs.get("mask_map"))

            # Check if StingrayPBS was created (default)
            surface_shader = pm.listConnections(
                node.surfaceShader, source=True, destination=False
            )
            self.assertTrue(surface_shader)
            self.assertEqual(mtk.NodeUtils.get_type(surface_shader[0]), "StingrayPBS")

    def test_config_override_arg(self):
        """Test overriding config with explicit argument."""
        config = {"shader_type": "standard_surface"}

        with patch(
            "pythontk.img_utils.map_factory.MapFactory.prepare_maps"
        ) as mock_prepare:
            mock_prepare.return_value = self.textures

            # Override with explicit arg
            node = self.shader.create_network(
                self.textures,
                name="test_override",
                config=config,
                shader_type="stingray",
            )

            surface_shader = pm.listConnections(
                node.surfaceShader, source=True, destination=False
            )
            self.assertTrue(surface_shader)
            self.assertEqual(mtk.NodeUtils.get_type(surface_shader[0]), "StingrayPBS")

    def test_config_override_kwarg(self):
        """Test overriding config with kwarg."""
        config = {"shader_type": "standard_surface"}

        with patch(
            "pythontk.img_utils.map_factory.MapFactory.prepare_maps"
        ) as mock_prepare:
            mock_prepare.return_value = self.textures

            # Override with kwarg
            node = self.shader.create_network(
                self.textures,
                name="test_override_kwarg",
                config=config,
                shader_type="stingray",
            )

            surface_shader = pm.listConnections(
                node.surfaceShader, source=True, destination=False
            )
            self.assertTrue(surface_shader)
            self.assertEqual(mtk.NodeUtils.get_type(surface_shader[0]), "StingrayPBS")
