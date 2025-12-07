import unittest
import pymel.core as pm
from base_test import MayaTkTestCase
from mayatk.mat_utils.shader_templates._shader_templates import GraphSaver


class TestDebugHistory(MayaTkTestCase):
    def test_debug_history(self):
        # Create a simple network
        shader = pm.shadingNode("StingrayPBS", asShader=True, name="test_shader")
        sg = pm.sets(renderable=True, noSurfaceShader=True, empty=True, name="test_sg")
        pm.connectAttr(shader.outColor, sg.surfaceShader)

        file_node = pm.shadingNode("file", asTexture=True, name="test_file")
        # StingrayPBS attributes might vary, let's check or use a known one
        if not shader.hasAttr("colorMap"):
            # Fallback for standard surface if Stingray not available or different version
            shader = pm.shadingNode("lambert", asShader=True, name="test_lambert")
            pm.connectAttr(shader.outColor, sg.surfaceShader, force=True)
            pm.connectAttr(file_node.outColor, shader.color)
        else:
            pm.connectAttr(file_node.outColor, shader.colorMap)

        output_log = "O:/Cloud/Code/_scripts/mayatk/test/debug_output.txt"
        with open(output_log, "w") as f:
            f.write(f"Created network: {file_node} -> {shader} -> {sg}\n")

            # Test listHistory on Shader
            history_shader = pm.listHistory(shader)
            f.write(f"History of Shader: {history_shader}\n")

            # Test listHistory on SG
            history_sg = pm.listHistory(sg)
            f.write(f"History of SG: {history_sg}\n")

            # Test GraphSaver logic
            saver = GraphSaver()

            # Case 1: Save from Shader
            f.write("\n--- Saving from Shader ---\n")
            nodes_shader = pm.listHistory([shader])
            filtered_nodes_shader = [
                n
                for n in nodes_shader
                if n.nodeType() not in ["shadingEngine", "materialInfo"]
            ]
            f.write(f"Filtered nodes (Shader): {filtered_nodes_shader}\n")
            graph_shader = saver.collect_graph(filtered_nodes_shader)
            f.write(f"Graph keys (Shader): {list(graph_shader.keys())}\n")

            # Case 2: Save from SG
            f.write("\n--- Saving from SG ---\n")
            nodes_sg = pm.listHistory([sg])
            filtered_nodes_sg = [
                n
                for n in nodes_sg
                if n.nodeType() not in ["shadingEngine", "materialInfo"]
            ]
            f.write(f"Filtered nodes (SG): {filtered_nodes_sg}\n")
            graph_sg = saver.collect_graph(filtered_nodes_sg)
            f.write(f"Graph keys (SG): {list(graph_sg.keys())}\n")

            # Case 3: Full Round Trip
            f.write("\n--- Full Round Trip ---\n")
            import tempfile
            import os
            import importlib
            import mayatk.mat_utils.shader_templates._shader_templates

            importlib.reload(mayatk.mat_utils.shader_templates._shader_templates)
            from mayatk.mat_utils.shader_templates._shader_templates import (
                ShaderTemplates,
            )

            tmp_file = os.path.join(tempfile.gettempdir(), "debug_template.yaml")
            ShaderTemplates.save_template(
                [shader], tmp_file, exclude_types=["shadingEngine", "materialInfo"]
            )

            # Clear scene
            pm.delete(shader, sg, file_node)

            # Capture stdout during restore
            import sys
            from io import StringIO

            captured_output = StringIO()
            original_stdout = sys.stdout
            sys.stdout = captured_output

            try:
                # Restore
                restored_nodes = ShaderTemplates.restore_template(tmp_file)
                f.write(f"Restored nodes: {restored_nodes}\n")
            except Exception as e:
                f.write(f"Restore failed: {e}\n")
            finally:
                sys.stdout = original_stdout

            f.write("\n--- Captured Stdout ---\n")
            f.write(captured_output.getvalue())
