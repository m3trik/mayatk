# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.mat_utils.marmoset_bridge.

Regression coverage for the Maya-side of the bridge -- export, manifest,
template rendering, and the UI's resize-on-template-switch behavior. The
Toolbag invocation itself is intentionally not exercised here (it needs the
external executable); ``test/mock_tests/test_marmoset_bridge.py`` covers the
pure-Python layers.

Tests run inside a live Maya session via ``run_tests.py`` and catch:

- ``fbxmaya`` plugin not pre-loaded in interactive Maya.
- A real shading graph survives the MatManifest -> JSON round-trip.
- Every bundled template renders to valid Python with no placeholder tokens
  surviving.
- The UI window shrinks/grows when the selected template's parameter
  references change.
"""
import ast
import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import maya.cmds as cmds

from mayatk.mat_utils.marmoset_bridge._marmoset_bridge import (
    MarmosetBridge,
    SEND_TO,
    ROUNDTRIP,
    _TEMPLATE_DIR,
    list_template_modes,
)
from mayatk.mat_utils.marmoset_bridge import parameters as _params

from base_test import MayaTkTestCase


class TestMarmosetBridgeRender(MayaTkTestCase):
    """No Toolbag needed: render every template against a real Maya scene."""

    def setUp(self):
        super().setUp()
        self.out_dir = tempfile.mkdtemp(prefix="marmoset_test_")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.out_dir, ignore_errors=True)
        super().tearDown()

    def test_every_template_mode_renders_and_parses(self):
        """Every declared (template, mode) pair must render to valid Python."""
        bridge = MarmosetBridge()
        pairs = list_template_modes()
        self.assertTrue(pairs, "No bundled templates found.")

        for stem, mode in pairs:
            with self.subTest(template=stem, mode=mode):
                rendered = bridge.render_template(
                    template=stem,
                    mode=mode,
                    fbx_path=os.path.join(self.out_dir, "x.fbx"),
                    manifest_path=os.path.join(self.out_dir, "x.materials.json"),
                    output_dir=self.out_dir,
                )
                self.assertIsNotNone(rendered, f"{stem} ({mode}) did not render.")
                try:
                    ast.parse(rendered)
                except SyntaxError as e:
                    self.fail(f"{stem} ({mode}) produced invalid Python: {e}")

                for key in _params.PARAMS:
                    self.assertNotIn(
                        f"__{key}__",
                        rendered,
                        f"Placeholder __{key}__ leaked into {stem}.py ({mode})",
                    )

    def test_roundtrip_mode_forces_save_and_quit(self):
        """Roundtrip mode wires SAVE_PATH + SHOULD_QUIT into the rendered script."""
        bridge = MarmosetBridge()
        rendered = bridge.render_template(
            template="bake",
            mode=ROUNDTRIP,
            fbx_path=os.path.join(self.out_dir, "scene.fbx"),
            manifest_path=os.path.join(self.out_dir, "scene.materials.json"),
            output_dir=self.out_dir,
        )
        self.assertIn("SHOULD_QUIT = True", rendered)
        self.assertIn("scene.tbscene", rendered)


class TestMarmosetBridgeExport(MayaTkTestCase):
    """Validate the export half end-to-end against a live Maya session.

    AppLauncher.launch is mocked across this suite so a Toolbag install on
    the test machine cannot accidentally pop a real Toolbag window when the
    bridge falls through to PATH candidates.
    """

    def setUp(self):
        super().setUp()
        self.out_dir = tempfile.mkdtemp(prefix="marmoset_test_")
        self._launch_patch = unittest.mock.patch(
            "mayatk.mat_utils.marmoset_bridge._marmoset_bridge.AppLauncher.launch",
            return_value=None,
        )
        self._launch_patch.start()

    def tearDown(self):
        import shutil

        self._launch_patch.stop()
        shutil.rmtree(self.out_dir, ignore_errors=True)
        super().tearDown()

    def test_send_loads_fbx_plugin_and_writes_artefacts(self):
        """send() must load fbxmaya, write the FBX, and emit a valid script."""
        if cmds.pluginInfo("fbxmaya", query=True, loaded=True):
            try:
                cmds.unloadPlugin("fbxmaya", force=True)
            except RuntimeError:
                self.skipTest("fbxmaya cannot be unloaded in this session.")

        cube = cmds.polyCube(name="marmoset_test_cube")[0]

        bridge = MarmosetBridge(toolbag_path="not-used.exe")
        result = bridge.send(
            objects=[cube],
            output_dir=self.out_dir,
            output_name="scene",
            template="bake",
            mode=SEND_TO,
            toolbag_exe=None,  # blocked: launch returns None via patched AppLauncher
        )

        # send_to with a launch that returns None counts as a failure --
        # the bridge surfaces that as result=None. Even so, FBX and
        # manifest should have been written *before* the launch attempt.
        self.assertTrue(
            cmds.pluginInfo("fbxmaya", query=True, loaded=True),
            "Bridge should have loaded fbxmaya before exporting.",
        )

        fbx_path = Path(self.out_dir) / "scene.fbx"
        manifest_path = Path(self.out_dir) / "scene.materials.json"
        script_path = Path(self.out_dir) / "scene_bake_send_to.py"

        self.assertTrue(fbx_path.is_file(), f"FBX not written: {fbx_path}")
        self.assertGreater(fbx_path.stat().st_size, 0, "FBX is empty.")
        self.assertTrue(manifest_path.is_file(), f"Manifest missing: {manifest_path}")
        self.assertTrue(script_path.is_file(), f"Script missing: {script_path}")

        with open(manifest_path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        self.assertIn("materials", payload)

        rendered = script_path.read_text(encoding="utf-8")
        ast.parse(rendered)
        for key in _params.PARAMS:
            self.assertNotIn(f"__{key}__", rendered)

        # result is None when launch fails -- that's expected for this mock.
        self.assertIsNone(result)

    def test_roundtrip_reports_newly_generated_maps(self):
        """Roundtrip should diff output_dir contents and surface the new files.

        We mock ``AppLauncher.run`` so it creates a couple of fake bake maps
        in *output_dir*, simulating what a real Toolbag bake would emit.
        """
        cube = cmds.polyCube(name="marmoset_roundtrip_cube")[0]

        bake_root = Path(self.out_dir)

        def fake_run(exe, args=None, cwd=None, timeout=None):
            # Pretend Toolbag baked two maps.
            (bake_root / "bake_Normal.tga").write_bytes(b"")
            (bake_root / "bake_AmbientOcclusion.tga").write_bytes(b"")
            r = unittest.mock.MagicMock()
            r.returncode = 0
            r.stdout = ""
            r.stderr = ""
            return r

        with unittest.mock.patch(
            "mayatk.mat_utils.marmoset_bridge._marmoset_bridge.AppLauncher.run",
            side_effect=fake_run,
        ):
            bridge = MarmosetBridge(toolbag_path="fake_toolbag.exe")
            result = bridge.send(
                objects=[cube],
                output_dir=self.out_dir,
                output_name="rt",
                template="bake",
                mode=ROUNDTRIP,
            )

        self.assertIsNotNone(result, "Roundtrip returned None unexpectedly")
        self.assertEqual(result["mode"], ROUNDTRIP)
        outputs = result.get("outputs") or []
        leaf_names = sorted(os.path.basename(p) for p in outputs)
        self.assertIn("bake_Normal.tga", leaf_names)
        self.assertIn("bake_AmbientOcclusion.tga", leaf_names)

    def test_send_with_explicit_material_path_propagates_to_manifest(self):
        """A textured standardSurface gets baseColor recorded in the manifest."""
        cube = cmds.polyCube(name="marmoset_textured")[0]

        shader = cmds.shadingNode("standardSurface", asShader=True, name="M_Test")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{shader}SG"
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(cube, edit=True, forceElement=sg)

        # Bind a real file node with a path to baseColor.
        tex_path = (Path(self.out_dir) / "test_diffuse.png").as_posix()
        Path(tex_path).write_bytes(b"")  # empty stub is fine for path serialisation
        file_node = cmds.shadingNode("file", asTexture=True, name="file_M_Test_BC")
        cmds.setAttr(f"{file_node}.fileTextureName", tex_path, type="string")
        cmds.connectAttr(
            f"{file_node}.outColor", f"{shader}.baseColor", force=True
        )

        bridge = MarmosetBridge(toolbag_path="not-used.exe")
        bridge.send(
            objects=[cube],
            output_dir=self.out_dir,
            output_name="scene",
            template="import",
            mode=SEND_TO,
        )

        manifest = json.loads(
            (Path(self.out_dir) / "scene.materials.json").read_text(encoding="utf-8")
        )
        self.assertIn("materials", manifest)
        mat_entry = manifest["materials"].get(shader)
        self.assertIsNotNone(
            mat_entry, f"Expected '{shader}' in manifest, got {list(manifest['materials'])}"
        )
        # MatManifest preserves Maya's native path separator (\\ on Windows);
        # normalize both sides before comparing so the test is OS-portable.
        self.assertEqual(
            os.path.normpath(mat_entry.get("baseColor", "")),
            os.path.normpath(tex_path),
        )


class TestMarmosetBridgeUiResize(MayaTkTestCase):
    """The window must shrink/grow when the active template's parameters change."""

    def test_window_height_tracks_visible_param_rows(self):
        """Switching templates hides/shows rows and the window follows."""
        from qtpy import QtWidgets
        from uitk import Switchboard
        from mayatk.mat_utils.marmoset_bridge.marmoset_bridge_slots import (
            MarmosetBridgeSlots,
        )
        from mayatk.mat_utils.marmoset_bridge import _marmoset_bridge as bridge_mod
        from mayatk.mat_utils.marmoset_bridge import parameters as _p

        sb = Switchboard(
            ui_source=str(bridge_mod._PKG_DIR),
            slot_source=MarmosetBridgeSlots,
        )
        ui = sb.loaded_ui.marmoset_bridge
        ui.restore_window_size = False
        ui.show()
        QtWidgets.QApplication.processEvents()
        ui.is_initialized = True

        templates = sorted(p.stem for p in bridge_mod._TEMPLATE_DIR.glob("*.py"))
        if len(templates) < 2:
            self.skipTest("Need at least two bundled templates to compare heights.")

        def row_count(stem):
            path = bridge_mod._TEMPLATE_DIR / f"{stem}.py"
            return len(_p.referenced_keys(path.read_text(encoding="utf-8")))

        sorted_by_rows = sorted(templates, key=row_count)
        few, many = sorted_by_rows[0], sorted_by_rows[-1]
        if row_count(few) == row_count(many):
            self.skipTest("All bundled templates reference the same param count.")

        cmb = ui.cmb000

        def index_for_stem(stem):
            """First combo index whose itemData = (stem, <any mode>)."""
            for i in range(cmb.count()):
                data = cmb.itemData(i)
                if isinstance(data, tuple) and data[0] == stem:
                    return i
            raise AssertionError(f"No combo entry for template stem '{stem}'.")

        cmb.setCurrentIndex(index_for_stem(many))
        QtWidgets.QApplication.processEvents()
        ui.resize(ui.width(), 800)
        QtWidgets.QApplication.processEvents()
        height_many = ui.height()

        cmb.setCurrentIndex(index_for_stem(few))
        for _ in range(5):
            QtWidgets.QApplication.processEvents()
        height_few = ui.height()

        ui.close()
        ui.deleteLater()

        self.assertLess(
            height_few,
            height_many,
            f"Window did not shrink: '{many}' ({row_count(many)} rows) "
            f"@ {height_many}px -> '{few}' ({row_count(few)} rows) "
            f"@ {height_few}px.",
        )


if __name__ == "__main__":
    unittest.main()
