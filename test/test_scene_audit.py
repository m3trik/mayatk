import unittest
import os
import tempfile

# Try to initialize QApplication to avoid "Cannot create a QWidget without QApplication" error
# which might be triggered by mayatk imports via pymel/userSetup
try:
    from PySide2.QtWidgets import QApplication

    if not QApplication.instance():
        app = QApplication([])
except ImportError:
    try:
        from PySide6.QtWidgets import QApplication

        if not QApplication.instance():
            app = QApplication([])
    except ImportError:
        pass

import pymel.core as pm
from mayatk.core_utils.diagnostics.scene_diag import (
    SceneAnalyzer,
    SceneDiagnostics,
    AuditProfile,
)
from base_test import MayaTkTestCase


class TestSceneDiagnostics(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.analyzer = SceneAnalyzer()

    def test_clean_scene(self):
        """Test analysis on a clean scene (simple cube)."""
        cube = pm.polyCube(name="CleanCube")[0]
        records = self.analyzer.analyze([cube])
        report = self.analyzer.generate_report(records)

        self.assertEqual(report.total_meshes, 1)
        self.assertEqual(report.total_tris, 12)
        self.assertEqual(len(report.top_offenders), 1)
        self.assertEqual(report.top_offenders[0].score, 0)  # Should be perfect

    def test_high_poly(self):
        """Test detection of high poly meshes."""
        # 100x100 sphere is approx 19800 tris (poles are tris)
        sphere = pm.polySphere(
            name="DenseSphere", subdivisionsX=100, subdivisionsY=100
        )[0]

        # Use strict profile to ensure failure
        profile = AuditProfile(max_tris=10000)
        records = self.analyzer.analyze([sphere], profile=profile)
        report = self.analyzer.generate_report(records)
        rec = report.top_offenders[0]

        self.assertTrue(rec.mesh.tris >= 19000)
        self.assertTrue(rec.score > 0)
        self.assertIn("High Poly", str(rec.findings))

    def test_ngons(self):
        """Test detection of N-gons."""
        # Create a pentagon (5-sided face)
        points = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0.5, 1.5, 0), (0, 1, 0)]
        plane = pm.polyCreateFacet(p=points, name="NgonFace")[0]

        records = self.analyzer.analyze([plane])
        report = self.analyzer.generate_report(records)
        rec = report.top_offenders[0]

        self.assertTrue(rec.mesh.ngons > 0)
        self.assertTrue(rec.score > 0)
        self.assertIn("N-gons", str(rec.score_breakdown))

    def test_multi_material(self):
        """Test detection of multiple material slots."""
        cube = pm.polyCube()[0]

        mat1 = pm.shadingNode("lambert", asShader=True, name="Mat1")
        sg1 = pm.sets(renderable=True, noSurfaceShader=True, empty=True, name="SG1")
        mat1.outColor >> sg1.surfaceShader
        pm.sets(sg1, forceElement=cube)

        mat2 = pm.shadingNode("lambert", asShader=True, name="Mat2")
        sg2 = pm.sets(renderable=True, noSurfaceShader=True, empty=True, name="SG2")
        mat2.outColor >> sg2.surfaceShader
        pm.sets(sg2, forceElement=cube.f[0])

        # Use strict profile
        profile = AuditProfile(max_slots=1)
        records = self.analyzer.analyze([cube], profile=profile)
        report = self.analyzer.generate_report(records)
        rec = report.top_offenders[0]

        self.assertEqual(rec.material.slot_count, 2)
        self.assertTrue(rec.score > 0)
        self.assertIn("Draw Call Split", str(rec.score_breakdown))

    def test_global_texture_usage(self):
        """Test global vs local texture usage counting."""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(b"fake image data")
            tex_path = tmp.name.replace("\\", "/")

        try:
            cube1 = pm.polyCube(name="Cube1")[0]
            cube2 = pm.polyCube(name="Cube2")[0]

            file_node = pm.shadingNode("file", asTexture=True)
            file_node.fileTextureName.set(tex_path)

            mat = pm.shadingNode("lambert", asShader=True)
            file_node.outColor >> mat.color

            sg = pm.sets(renderable=True, noSurfaceShader=True, empty=True)
            mat.outColor >> sg.surfaceShader
            pm.sets(sg, forceElement=[cube1, cube2])

            records = self.analyzer.analyze([cube1, cube2])
            report = self.analyzer.generate_report(records)

            rec1 = next(r for r in report.top_offenders if r.transform == "Cube1")

            self.assertEqual(rec1.material.unique_paths_local, 0)
            self.assertEqual(rec1.material.unique_paths_scene, 1)

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp2:
                tmp2.write(b"fake image data 2")
                tex_path2 = tmp2.name.replace("\\", "/")

            file_node2 = pm.shadingNode("file", asTexture=True)
            file_node2.fileTextureName.set(tex_path2)
            mat2 = pm.shadingNode("lambert", asShader=True)
            file_node2.outColor >> mat2.color
            sg2 = pm.sets(renderable=True, noSurfaceShader=True, empty=True)
            mat2.outColor >> sg2.surfaceShader
            pm.sets(sg2, forceElement=cube1)

            records = self.analyzer.analyze([cube1, cube2])
            report = self.analyzer.generate_report(records)
            rec1 = next(r for r in report.top_offenders if r.transform == "Cube1")

            self.assertEqual(rec1.material.unique_paths_local, 1)

        finally:
            if os.path.exists(tex_path):
                os.remove(tex_path)
            if "tex_path2" in locals() and os.path.exists(tex_path2):
                os.remove(tex_path2)

    def test_print_report(self):
        """Test printing the report to ensure no runtime errors."""
        # Test with empty report
        records = self.analyzer.analyze([])
        report = self.analyzer.generate_report(records)
        try:
            self.analyzer.print_report(report)
        except Exception as e:
            self.fail(f"print_report failed with empty report: {e}")

        # Test with populated report
        cube = pm.polyCube(name="PrintCube")[0]
        records = self.analyzer.analyze([cube])
        report = self.analyzer.generate_report(records)
        try:
            self.analyzer.print_report(report)
        except Exception as e:
            self.fail(f"print_report failed with populated report: {e}")

    def test_oversized_texture_logic(self):
        """Test that shared oversized textures are not penalized as heavily."""
        # Create two cubes
        cube1 = pm.polyCube(name="Cube1")[0]
        cube2 = pm.polyCube(name="Cube2")[0]

        # Create a material and assign to both
        mat = pm.shadingNode("lambert", asShader=True, name="SharedMat")
        sg = pm.sets(renderable=True, noSurfaceShader=True, empty=True, name="SharedSG")
        mat.outColor >> sg.surfaceShader
        pm.sets(sg, forceElement=cube1)
        pm.sets(sg, forceElement=cube2)

        # Mock _analyze_material_node to return a large texture
        original_method = self.analyzer._analyze_material_node

        def mock_analyze_material_node(mat_node):
            # Check if the node name matches (it might be a PyNode or string)
            if mat_node and mat_node.name() == mat.name():
                return {
                    "transparent": False,
                    "type": "lambert",
                    "unpacked_pbr": False,
                    "textures": [
                        {
                            "path": "c:/fake/4k_atlas.png",
                            "res": [4096, 4096],
                            "size_mb": 16.0,
                            "node": "file1",
                        }
                    ],
                    "missing_textures": 0,
                }
            return original_method(mat_node)

        # Patch the method
        self.analyzer._analyze_material_node = mock_analyze_material_node

        # Use strict profile
        profile = AuditProfile(max_tex_res=2048)

        # 1. Analyze BOTH cubes (Shared context)
        records = self.analyzer.analyze([cube1, cube2], profile=profile)
        report = self.analyzer.generate_report(records)

        # Check Cube1 findings
        # Note: shape name might be Cube1Shape or similar
        rec1 = next(r for r in report.top_offenders if "Cube1" in r.mesh.shape_name)

        # Should NOT have "Oversized Texture" because it's shared (count=2)
        # Should have "Max texture dimension"
        findings_str = str(rec1.findings)
        self.assertNotIn("Oversized Texture", findings_str)
        self.assertIn("Max texture dimension", findings_str)

        # 2. Analyze ONLY Cube1 (Unique context)
        # We must remove cube2 so the texture becomes truly unique to cube1
        # Otherwise, the analyzer correctly sees it's shared with another object (even if unselected)
        pm.delete(cube2)

        # If we only analyze Cube1, the texture appears unique to the selection scope (count=1)
        records_single = self.analyzer.analyze([cube1], profile=profile)
        report_single = self.analyzer.generate_report(records_single)
        rec1_single = report_single.top_offenders[0]

        findings_str_single = str(rec1_single.findings)
        self.assertIn("Oversized Texture", findings_str_single)


if __name__ == "__main__":
    unittest.main()
