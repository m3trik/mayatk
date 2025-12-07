# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.mat_utils.shader_templates module

Tests for ShaderTemplates functionality including:
- Graph collection and saving
- Graph restoration
- Texture map handling and resolution
- Complex network round-trips
- Attribute filtering and type conversion
"""
import unittest
import os
import tempfile
import shutil
import pymel.core as pm
from base_test import MayaTkTestCase
from mayatk.mat_utils.shader_templates._shader_templates import (
    GraphCollector,
    GraphSaver,
    GraphRestorer,
    ShaderTemplates,
)
from mayatk.node_utils._node_utils import NodeUtils


class TestShaderTemplates(MayaTkTestCase):
    """Comprehensive tests for ShaderTemplates class."""

    def setUp(self):
        """Set up test environment."""
        # Override to avoid pm.newFile(force=True) which might kill the test runner connection
        # in some environments, but we'll try to be safe.
        self.temp_dir = tempfile.mkdtemp()
        self.template_path = os.path.join(self.temp_dir, "test_template.yaml")
        self.nodes_to_delete = []

    def tearDown(self):
        """Clean up test environment."""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

        if self.nodes_to_delete:
            pm.delete([n for n in self.nodes_to_delete if pm.objExists(n)])

    # -------------------------------------------------------------------------
    # Core Functionality Tests
    # -------------------------------------------------------------------------

    def test_collect_and_save_graph(self):
        """Test collecting and saving a simple shader graph."""
        # Create a simple graph
        shader = pm.shadingNode("lambert", asShader=True, name="test_lambert")
        file_node = pm.shadingNode("file", asTexture=True, name="test_file")
        pm.connectAttr(file_node.outColor, shader.color)
        self.nodes_to_delete.extend([shader, file_node])

        # Collect graph
        collector = GraphCollector()
        graph_info = collector.collect_graph([shader, file_node])

        self.assertTrue(len(graph_info) >= 2)

        # Check if connections are captured
        connections_found = False
        for node_data in graph_info.values():
            for conn in node_data["connections"]:
                if "outColor" in conn["source"] and "color" in conn["target"]:
                    connections_found = True
        self.assertTrue(
            connections_found, f"Connections not found. Graph info: {graph_info}"
        )

        # Save graph
        saver = GraphSaver()
        saver.save_graph([shader, file_node], self.template_path)
        self.assertTrue(os.path.exists(self.template_path))

    def test_restore_graph(self):
        """Test restoring a saved graph."""
        # Create a template file manually or via save
        shader = pm.shadingNode("blinn", asShader=True, name="original_blinn")
        file_node = pm.shadingNode("file", asTexture=True, name="original_file")
        pm.connectAttr(file_node.outColor, shader.color)

        saver = GraphSaver()
        saver.save_graph([shader, file_node], self.template_path)

        # Clear scene
        pm.delete(shader, file_node)
        self.assertFalse(pm.objExists("original_blinn"))

        # Restore graph
        restorer = GraphRestorer(self.template_path, [], name="restored_shader")
        restorer.restore_graph()

        # Track restored nodes for cleanup
        self.nodes_to_delete.extend(restorer.nodes.values())

        # Verify nodes are created
        blinns = pm.ls(type="blinn")
        files = pm.ls(type="file")

        self.assertTrue(len(blinns) > 0)
        self.assertTrue(len(files) > 0)

        # Verify connection
        connected = False
        for blinn in blinns:
            inputs = blinn.color.inputs()
            if inputs and inputs[0] in files:
                connected = True
                break
        self.assertTrue(connected)

    def test_complex_network_roundtrip(self):
        """Test saving and restoring a complex network with multiple node types."""
        # Create nodes
        shader = pm.shadingNode("lambert", asShader=True, name="test_lambert")
        tex = pm.shadingNode("file", asTexture=True, name="test_file")
        place = pm.shadingNode("place2dTexture", asUtility=True, name="test_place2d")
        bump = pm.shadingNode("bump2d", asUtility=True, name="test_bump")

        # Set some values
        pm.setAttr(shader.color, (1, 0, 0))
        pm.setAttr(place.rotateUV, 45)
        pm.setAttr(bump.bumpDepth, 0.5)

        # Connect
        pm.connectAttr(place.outUV, tex.uvCoord)
        pm.connectAttr(place.outUvFilterSize, tex.uvFilterSize)
        pm.connectAttr(tex.outAlpha, bump.bumpValue)
        pm.connectAttr(bump.outNormal, shader.normalCamera)

        # Save
        template_path = os.path.join(self.temp_dir, "complex_test.yaml")
        nodes = [shader, tex, place, bump]

        ShaderTemplates.save_template(nodes, template_path)

        # Clear scene
        pm.delete(nodes)

        # Restore
        restored_nodes = ShaderTemplates.restore_template(template_path)
        self.nodes_to_delete.extend(restored_nodes.values())

        # Verify
        self.assertTrue(len(restored_nodes) >= 4, "Should restore at least 4 nodes.")

        # Check if place2d rotation is preserved
        place_node = next(
            (n for n in restored_nodes.values() if n.nodeType() == "place2dTexture"),
            None,
        )
        self.assertIsNotNone(place_node, "place2dTexture node should be restored.")

        rot = pm.getAttr(place_node.rotateUV)
        self.assertAlmostEqual(
            rot, 45.0, delta=1e-5, msg="Attribute value (rotateUV) should be preserved."
        )

    # -------------------------------------------------------------------------
    # Texture and File Node Tests
    # -------------------------------------------------------------------------

    def test_file_node_attributes_and_connections(self):
        """Test that file nodes retain their attributes and connections after restore."""
        # Create a file node with specific settings
        file_node = pm.shadingNode("file", asTexture=True, name="attr_test_file")
        shader = pm.shadingNode("lambert", asShader=True, name="attr_test_shader")

        # Set attributes
        test_path = os.path.join(self.temp_dir, "test_texture.png").replace("\\", "/")
        # Create a dummy file so it's valid
        with open(test_path, "w") as f:
            f.write("dummy")

        pm.setAttr(file_node.fileTextureName, test_path)
        pm.setAttr(file_node.alphaGain, 0.5)

        # Connect
        pm.connectAttr(file_node.outColor, shader.color)

        # Save
        ShaderTemplates.save_template([shader, file_node], self.template_path)

        # DEBUG: Copy YAML to a visible location
        try:
            shutil.copy(
                self.template_path, "O:/Cloud/Code/_scripts/debug_template.yaml"
            )
        except Exception:
            pass

        # Delete
        pm.delete(shader, file_node)

        # Restore
        restored_nodes = ShaderTemplates.restore_template(self.template_path)
        self.nodes_to_delete.extend(restored_nodes.values())

        # Find restored file node
        restored_file = None
        for node in restored_nodes.values():
            if pm.nodeType(node) == "file":
                restored_file = node
                break

        self.assertIsNotNone(restored_file, "File node should be restored")

        # Verify attributes
        restored_path = pm.getAttr(restored_file.fileTextureName)
        # Normalize paths for comparison
        self.assertEqual(os.path.normpath(restored_path), os.path.normpath(test_path))

        self.assertAlmostEqual(pm.getAttr(restored_file.alphaGain), 0.5)

        # Verify connection
        outputs = restored_file.outColor.outputs()
        self.assertTrue(len(outputs) > 0, "File node should be connected to shader")
        self.assertEqual(pm.nodeType(outputs[0]), "lambert")

    # -------------------------------------------------------------------------
    # Utility and Edge Case Tests
    # -------------------------------------------------------------------------

    def test_exclude_types(self):
        """Test excluding specific node types during save."""
        shader = pm.shadingNode("lambert", asShader=True, name="test_lambert")
        sg = pm.sets(renderable=True, noSurfaceShader=True, empty=True, name="testSG")
        pm.connectAttr(shader.outColor, sg.surfaceShader)
        self.nodes_to_delete.extend([shader, sg])

        saver = GraphSaver()
        saver.save_graph(
            [shader, sg], self.template_path, exclude_types=["shadingEngine"]
        )

        # Read the file back to check
        import yaml

        with open(self.template_path, "r") as f:
            data = yaml.safe_load(f)

        # Check that no shadingEngine is in the data
        has_sg = False
        for node_data in data.values():
            if node_data["type"] == "shadingEngine":
                has_sg = True
                break
        self.assertFalse(has_sg)

    def test_pymel_datatype_serialization(self):
        """Verify if PyMEL datatypes are converted to basic types."""
        node = pm.createNode("lambert")
        pm.setAttr(node.color, (0.1, 0.2, 0.3))
        self.nodes_to_delete.append(node)

        # Get attributes
        attrs = NodeUtils.get_node_attributes(
            node, exc_defaults=False, scalarAndArray=False
        )

        # Test conversion
        converted = GraphSaver._convert_to_basic_types(attrs)
        color_converted = converted.get("color")

        self.assertIsInstance(
            color_converted, (list, tuple), "Color should be converted to list/tuple."
        )
        self.assertNotIsInstance(
            color_converted, pm.dt.Color, "Color should not be a PyMEL datatype."
        )

    def test_list_attr_keyable_behavior(self):
        """Verify if keyable=False excludes keyable attributes (utility check)."""
        node = pm.createNode("lambert")
        pm.setAttr(node.color, (0.5, 0.5, 0.5))
        self.nodes_to_delete.append(node)

        # Default kwargs in NodeUtils.get_node_attributes
        kwargs = {
            "read": True,
            "hasData": True,
            "settable": True,
            "scalarAndArray": True,
            "keyable": False,
            "multi": True,
        }

        attrs = pm.listAttr(node, **kwargs)

        # Check if we can get keyable attributes by removing the restriction
        kwargs.pop("keyable")
        attrs_all = pm.listAttr(node, **kwargs)

        self.assertIn(
            "colorR",
            attrs_all,
            "'colorR' should be present when keyable arg is removed.",
        )

    def test_full_restoration_with_maps(self):
        """
        Extensive test to verify full graph restoration with correct map connections
        and no duplicate graphs.
        """
        from mayatk.mat_utils.stingray_arnold_shader import StingrayArnoldShader

        # 1. Setup Test Textures
        # We need to create dummy files that match the naming convention expected by TextureMapFactory
        tex_names = {
            "Base_Color": "Test_Base_Color.png",
            "Normal": "Test_Normal.png",
            "Metallic": "Test_Metallic.png",
            "Roughness": "Test_Roughness.png",
            "Ambient_Occlusion": "Test_AmbientOcclusion.png",
        }

        texture_paths = []
        for map_type, name in tex_names.items():
            path = os.path.join(self.temp_dir, name).replace("\\", "/")
            # Create dummy image content
            with open(path, "wb") as f:
                f.write(
                    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
                )
            texture_paths.append(path)

        # 2. Create Initial Network (Simulation of Generation)
        shader_gen = StingrayArnoldShader()
        result_node = shader_gen.create_network(
            textures=texture_paths, create_arnold=False
        )

        # result_node is likely the Shading Engine. Find the StingrayPBS node.
        original_pbs = None
        nodes_to_save = []

        if pm.nodeType(result_node) == "shadingEngine":
            # Find surface shader
            connections = pm.listConnections(result_node.surfaceShader)
            if connections:
                original_pbs = connections[0]
            nodes_to_save.append(result_node)
        else:
            original_pbs = result_node

        if original_pbs:
            nodes_to_save.extend(pm.listHistory(original_pbs))

        self.nodes_to_delete.extend(nodes_to_save)

        # 3. Save Template
        ShaderTemplates.save_template(
            nodes_to_save,
            self.template_path,
            exclude_types=[
                "aiStandardSurface",
                "aiImage",
                "aiNormalMap",
                "aiSkyDomeLight",
            ],
        )

        # 4. Clear Scene
        pm.delete(pm.ls(type="StingrayPBS"))
        pm.delete(pm.ls(type="file"))
        pm.delete(pm.ls(type="shadingEngine"))
        pm.delete(pm.ls(type="place2dTexture"))

        # 5. Restore Template
        restored_nodes = ShaderTemplates.restore_template(
            self.template_path, texture_paths=texture_paths
        )
        self.nodes_to_delete.extend(restored_nodes.values())

        # 6. Verification

        # A. Check for single StingrayPBS node
        pbs_nodes = pm.ls(type="StingrayPBS")
        self.assertEqual(len(pbs_nodes), 1, "Should have exactly one StingrayPBS node.")
        pbs_node = pbs_nodes[0]

        # B. Check for absence of Arnold nodes
        arnold_nodes = pm.ls(type="aiStandardSurface")
        self.assertEqual(
            len(arnold_nodes), 0, "Should have no aiStandardSurface nodes."
        )

        # C. Check Connections
        expected_connections = {
            "TEX_color_map": "Test_Base_Color.png",
            "TEX_normal_map": "Test_Normal.png",
            "TEX_metallic_map": "Test_Metallic.png",
            "TEX_roughness_map": "Test_Roughness.png",
            "TEX_ao_map": "Test_AmbientOcclusion.png",
        }

        for attr_name, tex_name in expected_connections.items():
            # Get input to the attribute
            if not hasattr(pbs_node, attr_name):
                self.fail(f"StingrayPBS node missing attribute: {attr_name}")

            attr = getattr(pbs_node, attr_name)
            inputs = attr.inputs()

            self.assertTrue(
                len(inputs) > 0,
                f"Attribute {attr_name} should have an input connection.",
            )
            input_node = inputs[0]
            self.assertEqual(
                pm.nodeType(input_node),
                "file",
                f"Input to {attr_name} should be a file node.",
            )

            # Check file path
            file_path = pm.getAttr(input_node.fileTextureName)
            self.assertTrue(
                file_path.replace("\\", "/").endswith(tex_name),
                f"File node for {attr_name} should point to {tex_name}, got {file_path}",
            )

    def test_partial_restoration_and_attributes(self):
        """
        Test restoring a template with only a subset of textures provided.
        Verifies that provided textures are assigned, and shader attributes are restored.
        """
        from mayatk.mat_utils.stingray_arnold_shader import StingrayArnoldShader

        # 1. Setup Test Textures (Full Set for Template Generation)
        tex_names = {
            "Base_Color": "Test_Base_Color.png",
            "Normal": "Test_Normal.png",
            "Metallic": "Test_Metallic.png",
            "Roughness": "Test_Roughness.png",
        }
        texture_paths = []
        for name in tex_names.values():
            path = os.path.join(self.temp_dir, name).replace("\\", "/")
            with open(path, "wb") as f:
                f.write(b"dummy")
            texture_paths.append(path)

        # 2. Generate and Save Template
        shader_gen = StingrayArnoldShader()
        result_node = shader_gen.create_network(
            textures=texture_paths, create_arnold=False
        )

        original_pbs = None
        nodes_to_save = []
        if pm.nodeType(result_node) == "shadingEngine":
            connections = pm.listConnections(result_node.surfaceShader)
            if connections:
                original_pbs = connections[0]
            nodes_to_save.append(result_node)
        else:
            original_pbs = result_node

        if original_pbs:
            nodes_to_save.extend(pm.listHistory(original_pbs))
            # Set a specific attribute value to test restoration
            if hasattr(original_pbs, "use_color_map"):
                pm.setAttr(original_pbs.use_color_map, 1.0)
            if hasattr(original_pbs, "use_metallic_map"):
                pm.setAttr(original_pbs.use_metallic_map, 1.0)

        self.nodes_to_delete.extend(nodes_to_save)

        ShaderTemplates.save_template(
            nodes_to_save, self.template_path, exclude_types=["aiStandardSurface"]
        )

        # 3. Clear Scene
        pm.delete(nodes_to_save)

        # 4. Restore with PARTIAL textures (Only Base Color)
        partial_textures = [p for p in texture_paths if "Base_Color" in p]

        restored_nodes = ShaderTemplates.restore_template(
            self.template_path, texture_paths=partial_textures
        )
        self.nodes_to_delete.extend(restored_nodes.values())

        # 5. Verify
        pbs_nodes = pm.ls(type="StingrayPBS")
        self.assertEqual(len(pbs_nodes), 1)
        pbs_node = pbs_nodes[0]

        # Check Base Color Connection (Should be updated)
        color_attr = getattr(pbs_node, "TEX_color_map", None)
        if color_attr:
            inputs = color_attr.inputs()
            self.assertTrue(len(inputs) > 0, "Base Color should be connected")
            file_node = inputs[0]
            path = pm.getAttr(file_node.fileTextureName)
            self.assertTrue(
                "Test_Base_Color.png" in path, "Base Color path should be updated"
            )

        # Check Attributes
        if hasattr(pbs_node, "use_color_map"):
            self.assertEqual(
                pm.getAttr(pbs_node.use_color_map), 1.0, "use_color_map should be 1.0"
            )

        # Check Shading Engine Connection
        outputs = pbs_node.outColor.outputs(type="shadingEngine")
        self.assertTrue(
            len(outputs) > 0, "StingrayPBS should be connected to a Shading Engine"
        )
