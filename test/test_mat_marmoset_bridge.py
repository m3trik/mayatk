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
    MarmosetEngine,
    MarmosetBridge,
    SEND_TO,
    ROUND_TRIP,
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
        pairs = MarmosetEngine.list_template_modes()
        self.assertTrue(pairs, "No bundled templates found.")

        for stem, mode in pairs:
            with self.subTest(template=stem, mode=mode):
                rendered = bridge.render_template(
                    template=stem,
                    mode=mode,
                    model_path=os.path.join(self.out_dir, "x.fbx"),
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
            mode=ROUND_TRIP,
            model_path=os.path.join(self.out_dir, "scene.fbx"),
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
            "mayatk.mat_utils.marmoset_bridge._marmoset_engine.AppLauncher.launch",
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

    @staticmethod
    def _fake_toolbag_bake(*map_stems):
        """An ``AppLauncher.run`` stand-in that writes *map_stems* as bake maps.

        Toolbag writes into the ``OUTPUT_DIR`` the rendered script declares --
        a local scratch dir, since the engine stages roundtrip bakes off the
        (possibly cloud-synced) destination and relocates them afterwards.
        """

        def fake_run(exe, args=None, cwd=None, timeout=None):
            import re

            script_body = Path(args[-1]).read_text(encoding="utf-8")
            bake_root = Path(re.search(r'OUTPUT_DIR = r"(.*)"', script_body).group(1))
            for stem in map_stems:
                (bake_root / f"bake_{stem}.tga").write_bytes(b"")
            r = unittest.mock.MagicMock()
            r.returncode = 0
            r.stdout = ""
            r.stderr = ""
            return r

        return fake_run

    def _temp_workspace(self):
        """Open a throwaway Maya project; restore the previous one on cleanup."""
        import shutil

        from mayatk.env_utils._env_utils import EnvUtils

        previous = cmds.workspace(query=True, rootDirectory=True)
        root = tempfile.mkdtemp(prefix="marmoset_ws_")
        EnvUtils.set_current_workspace(root)

        def _restore():
            if previous and os.path.isdir(previous):
                EnvUtils.set_current_workspace(previous)
            shutil.rmtree(root, ignore_errors=True)

        self.addCleanup(_restore)
        return root

    def test_roundtrip_reports_newly_generated_maps(self):
        """Roundtrip should diff the bake scratch dir and surface the new files.

        ``texture_dir`` is named explicitly so this stays a test of the
        relocation itself; where an *unset* one resolves to is the subject of
        :meth:`test_bake_maps_land_in_the_project_texture_folder`.
        """
        cube = cmds.polyCube(name="marmoset_roundtrip_cube")[0]

        with unittest.mock.patch(
            "mayatk.mat_utils.marmoset_bridge._marmoset_engine.AppLauncher.run",
            side_effect=self._fake_toolbag_bake("Normal", "AmbientOcclusion"),
        ):
            bridge = MarmosetBridge(toolbag_path="fake_toolbag.exe")
            result = bridge.send(
                objects=[cube],
                output_dir=self.out_dir,
                texture_dir=self.out_dir,
                output_name="rt",
                template="bake",
                mode=ROUND_TRIP,
            )

        self.assertIsNotNone(result, "Roundtrip returned None unexpectedly")
        self.assertEqual(result["mode"], ROUND_TRIP)
        outputs = result.get("outputs") or []
        leaf_names = sorted(os.path.basename(p) for p in outputs)
        # Relocation strips the template's constant 'bake_' stem so the
        # production files carry only the texture-set / material identity.
        self.assertIn("Normal.tga", leaf_names)
        self.assertIn("AmbientOcclusion.tga", leaf_names)
        for p in outputs:
            self.assertEqual(
                Path(p).resolve().parent,
                Path(self.out_dir).resolve(),
                "Roundtrip outputs must be relocated into the texture dir.",
            )

    def test_bake_maps_land_in_the_project_texture_folder(self):
        """Baked maps go to ``sourceimages/baked``, not beside the handoff files.

        Regression: with the maps written next to the scene, every one of them
        was external to sourceimages, so the exporter's texture consolidation
        copied them in -- straight onto the name of the SOURCE map that fed the
        bake. Refusing to clobber it, staging renamed each map ``_1``, ``_2``,
        ... and the scene ended up referencing the numbered copies. Landing
        them in the project's texture folder to begin with leaves that pass
        nothing to copy and nothing to rename.  Added: 2026-08-18
        """
        root = self._temp_workspace()
        cube = cmds.polyCube(name="marmoset_texdir_cube")[0]

        with unittest.mock.patch(
            "mayatk.mat_utils.marmoset_bridge._marmoset_engine.AppLauncher.run",
            side_effect=self._fake_toolbag_bake("MAT_Base_Color"),
        ):
            bridge = MarmosetBridge(toolbag_path="fake_toolbag.exe")
            result = bridge.send(
                objects=[cube],
                output_dir=self.out_dir,
                output_name="rt",
                template="bake",
                mode=ROUND_TRIP,
            )

        self.assertIsNotNone(result, "Roundtrip returned None unexpectedly")
        expected = Path(root) / "sourceimages" / MarmosetBridge.BAKED_TEXTURE_SUBDIR
        self.assertEqual(
            Path(result["texture_dir"]).resolve(),
            expected.resolve(),
            "Bake maps must be destined for the project's texture folder.",
        )
        outputs = result.get("outputs") or []
        self.assertTrue(outputs, "Roundtrip produced no maps")
        for p in outputs:
            self.assertEqual(Path(p).resolve().parent, expected.resolve())
            self.assertTrue(os.path.isfile(p), f"{p} was not written")
        # The handoff artifacts stay where the caller put them.
        self.assertEqual(
            Path(result["output_dir"]).resolve(), Path(self.out_dir).resolve()
        )
        self.assertFalse(
            [f for f in os.listdir(self.out_dir) if f.lower().endswith(".tga")],
            "Maps must not also be left beside the handoff artifacts.",
        )

    def test_rebake_files_its_maps_under_the_source_material_name(self):
        """A second roundtrip overwrites the first's maps instead of stacking.

        After bake 1 the target meshes wear ``<mat>_BAKED``, and Toolbag names
        each output after the FBX material -- so the run must file them under
        the source material or the project collects a whole second generation
        of maps that differ only by a suffix.  Added: 2026-08-18
        """
        root = self._temp_workspace()
        cube = cmds.polyCube(name="marmoset_rebake_cube")[0]
        shader = cmds.shadingNode("lambert", asShader=True, name="REBAKE_BAKED")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="REBAKE_BAKEDSG"
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(cube, edit=True, forceElement=sg)

        with unittest.mock.patch(
            "mayatk.mat_utils.marmoset_bridge._marmoset_engine.AppLauncher.run",
            side_effect=self._fake_toolbag_bake("REBAKE_BAKED_Base_Color"),
        ):
            bridge = MarmosetBridge(toolbag_path="fake_toolbag.exe")
            result = bridge.send(
                objects=[cube],
                output_dir=self.out_dir,
                output_name="rt",
                template="bake",
                mode=ROUND_TRIP,
                # The assignment half needs readable images; this test is about
                # where the FILES land, which is settled before that runs.
                params={"ASSIGN_MATERIAL": False},
            )

        self.assertIsNotNone(result, "Roundtrip returned None unexpectedly")
        baked = Path(root) / "sourceimages" / MarmosetBridge.BAKED_TEXTURE_SUBDIR
        landed = sorted(p.name for p in baked.iterdir())
        self.assertEqual(
            landed,
            ["REBAKE_Base_Color.tga"],
            "a re-bake's map must be filed under the source material name",
        )
        self.assertFalse(
            [n for n in landed if "BAKED" in n],
            "no map file may carry the material's _BAKED marker",
        )

    def test_explicit_texture_dir_wins_over_the_project_default(self):
        """A caller that names ``texture_dir`` keeps it, project or no project.

        Added: 2026-08-18
        """
        import shutil

        self._temp_workspace()
        chosen = tempfile.mkdtemp(prefix="marmoset_texdir_")
        self.addCleanup(shutil.rmtree, chosen, True)
        cube = cmds.polyCube(name="marmoset_texdir_explicit")[0]

        with unittest.mock.patch(
            "mayatk.mat_utils.marmoset_bridge._marmoset_engine.AppLauncher.run",
            side_effect=self._fake_toolbag_bake("MAT_Roughness"),
        ):
            bridge = MarmosetBridge(toolbag_path="fake_toolbag.exe")
            result = bridge.send(
                objects=[cube],
                output_dir=self.out_dir,
                texture_dir=chosen,
                output_name="rt",
                template="bake",
                mode=ROUND_TRIP,
            )

        self.assertIsNotNone(result, "Roundtrip returned None unexpectedly")
        self.assertEqual(Path(result["texture_dir"]).resolve(), Path(chosen).resolve())
        for p in result.get("outputs") or []:
            self.assertEqual(Path(p).resolve().parent, Path(chosen).resolve())

    def test_roundtrip_stages_its_handoff_in_temp_and_cleans_up(self):
        """With no Output Dir named, a bake roundtrip must not write beside the scene.

        Regression: the panel's blank Output Dir resolved to the scene/workspace
        dir, so every roundtrip left ``<scene>.fbx`` / ``.materials.json`` /
        ``.bake_pairs.json`` / the rendered script / ``.tbscene`` in the
        project next to the scene file. A roundtrip runs Toolbag BLOCKING and
        relocates its only durable output (the maps) into the texture folder,
        so those artifacts are intermediates the run itself consumes: they
        belong in a swept temp dir that the successful run then removes.
        Added: 2026-08-18
        """
        root = self._temp_workspace()
        cube = cmds.polyCube(name="marmoset_scratch_cube")[0]

        with unittest.mock.patch(
            "mayatk.mat_utils.marmoset_bridge._marmoset_engine.AppLauncher.run",
            side_effect=self._fake_toolbag_bake("MAT_Base_Color"),
        ):
            bridge = MarmosetBridge(toolbag_path="fake_toolbag.exe")
            result = bridge.send(
                objects=[cube],
                output_name="rt",
                template="bake",
                mode=ROUND_TRIP,
            )

        self.assertIsNotNone(result, "Roundtrip returned None unexpectedly")
        handoff = Path(result["output_dir"]).resolve()
        # Not in the project, and not beside the scene file.
        self.assertFalse(
            str(handoff).lower().startswith(str(Path(root).resolve()).lower()),
            f"Hand-off artifacts landed inside the project: {handoff}",
        )
        temp_root = str(Path(tempfile.gettempdir()).resolve()).lower()
        self.assertTrue(
            str(handoff).lower().startswith(temp_root),
            f"Hand-off dir is not under the system temp dir: {handoff}",
        )
        # ... and the successful run took it away with it.
        self.assertFalse(
            handoff.exists(), f"Hand-off scratch dir survived the run: {handoff}"
        )
        # The maps -- the run's only durable output -- are untouched.
        outputs = result.get("outputs") or []
        self.assertTrue(outputs, "Roundtrip produced no maps")
        for p in outputs:
            self.assertTrue(os.path.isfile(p), f"{p} did not survive the cleanup")

    def test_roundtrip_keeps_an_explicit_output_dir(self):
        """A caller/user that NAMES an Output Dir keeps its files. Added: 2026-08-18"""
        self._temp_workspace()
        cube = cmds.polyCube(name="marmoset_explicit_out")[0]

        with unittest.mock.patch(
            "mayatk.mat_utils.marmoset_bridge._marmoset_engine.AppLauncher.run",
            side_effect=self._fake_toolbag_bake("MAT_Base_Color"),
        ):
            bridge = MarmosetBridge(toolbag_path="fake_toolbag.exe")
            result = bridge.send(
                objects=[cube],
                output_dir=self.out_dir,
                output_name="rt",
                template="bake",
                mode=ROUND_TRIP,
            )

        self.assertIsNotNone(result, "Roundtrip returned None unexpectedly")
        self.assertEqual(
            Path(result["output_dir"]).resolve(), Path(self.out_dir).resolve()
        )
        self.assertTrue(
            (Path(self.out_dir) / "rt.fbx").is_file(),
            "An explicitly named Output Dir must keep its hand-off artifacts.",
        )

    def test_roundtrip_keeps_the_scratch_when_the_maps_land_in_it(self):
        """No project texture folder -> the maps ARE the scratch dir; keep it.

        The cleanup is guarded on where the delivered files actually landed:
        with no workspace, ``texture_dir`` falls back to the hand-off dir, and
        deleting it would destroy the bake.  Added: 2026-08-18
        """
        import shutil

        cube = cmds.polyCube(name="marmoset_no_project_cube")[0]

        # No project texture folder -> the engine keeps the maps in the run's
        # own output dir, which here IS the scratch.
        no_project = unittest.mock.patch.object(
            MarmosetBridge, "baked_texture_dir", classmethod(lambda cls: "")
        )
        no_project.start()
        self.addCleanup(no_project.stop)

        with unittest.mock.patch(
            "mayatk.mat_utils.marmoset_bridge._marmoset_engine.AppLauncher.run",
            side_effect=self._fake_toolbag_bake("MAT_Base_Color"),
        ):
            bridge = MarmosetBridge(toolbag_path="fake_toolbag.exe")
            result = bridge.send(
                objects=[cube],
                output_name="rt",
                template="bake",
                mode=ROUND_TRIP,
            )

        self.assertIsNotNone(result, "Roundtrip returned None unexpectedly")
        handoff = Path(result["output_dir"]).resolve()
        # The run root is the parent (``<prefix>_<tag>/handoff``); remove all of
        # it, and assert the shape rather than assuming it -- an rmtree on a
        # wrongly-derived parent would be a very bad way to find out.
        root = handoff.parent
        self.assertTrue(root.name.startswith(f"{MarmosetBridge.payload_prefix}_"))
        self.addCleanup(shutil.rmtree, str(root), True)
        outputs = result.get("outputs") or []
        self.assertTrue(outputs, "Roundtrip produced no maps")
        self.assertTrue(
            handoff.is_dir(),
            "The scratch dir holds the only copy of the maps; it must survive.",
        )
        for p in outputs:
            self.assertTrue(os.path.isfile(p), f"{p} was swept away with the scratch")

    def test_send_to_keeps_its_handoff_artifacts(self):
        """send_to is read by a DETACHED Toolbag; its files must outlive us.

        Added: 2026-08-18
        """
        cube = cmds.polyCube(name="marmoset_send_to_keep")[0]

        bridge = MarmosetBridge(toolbag_path="not-used.exe")
        bridge.send(
            objects=[cube],
            output_dir=self.out_dir,
            output_name="scene",
            template="bake",
            mode=SEND_TO,
        )
        self.assertTrue(
            (Path(self.out_dir) / "scene.fbx").is_file(),
            "send_to must leave its hand-off FBX on disk for Toolbag to read.",
        )

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
        cmds.connectAttr(f"{file_node}.outColor", f"{shader}.baseColor", force=True)

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
            mat_entry,
            f"Expected '{shader}' in manifest, got {list(manifest['materials'])}",
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
        from mayatk.mat_utils.marmoset_bridge import _marmoset_engine as engine_mod
        from mayatk.mat_utils.marmoset_bridge import parameters as _p

        sb = Switchboard(
            ui_source=str(engine_mod._PKG_DIR),
            slot_source=MarmosetBridgeSlots,
        )
        ui = sb.loaded_ui.marmoset_bridge
        ui.restore_window_size = False
        ui.show()
        QtWidgets.QApplication.processEvents()
        ui.is_initialized = True

        # Enumerate via the production discovery helper (not a raw *.py glob) so
        # the test applies the same "_"-prefixed filter the combo does — else the
        # templates/ package's __init__.py is picked up as a bogus "__init__"
        # template and has no combo entry.
        templates = sorted(p.stem for p in bridge_mod.MarmosetEngine.list_templates())
        if len(templates) < 2:
            self.skipTest("Need at least two bundled templates to compare heights.")

        def row_count(stem):
            path = bridge_mod._TEMPLATE_DIR / f"{stem}.py"
            return len(_p.Parameters.referenced_keys(path.read_text(encoding="utf-8")))

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


class TestBakeClassification(MayaTkTestCase):
    """High Poly set splitting + pairs-sidecar collision handling.

    Regression source: a retopo/UV-transfer scene whose source and target
    hierarchies reuse identical mesh names (|STATIC|HAMMER vs |HIGH|HAMMER).
    The leaf-keyed pairs sidecar silently let one side win, and suffix
    pairing had nothing to match -- the explicit High Poly set flow is the
    fix, with the sidecar collision now dropped loudly instead.
    """

    def tearDown(self):
        from mayatk.mat_utils.bake_sets import BakeSourceSet

        BakeSourceSet.clear()
        super().tearDown()

    @staticmethod
    def _grouped_cube(group_name, cube_name):
        grp = cmds.group(empty=True, name=group_name)
        cube = cmds.polyCube()[0]
        cube = cmds.parent(cube, grp)[0]
        cmds.rename(cube, cube_name)
        return cmds.ls(grp, long=True)[0]

    def test_pairs_manifest_drops_colliding_leaf_names(self):
        src = self._grouped_cube("pairs_src_high", "pairs_part")
        tgt = self._grouped_cube("pairs_tgt_low", "pairs_part")
        pairs = MarmosetBridge.build_bake_pairs_manifest([src, tgt], "_high", "_low")
        self.assertEqual(
            pairs,
            {},
            "Identical leaf names on both sides must be dropped, not "
            "silently overwritten.",
        )

    def test_pairs_manifest_classifies_distinct_names(self):
        src = self._grouped_cube("pairs2_src_high", "pairs2_a")
        tgt = self._grouped_cube("pairs2_tgt_low", "pairs2_b")
        pairs = MarmosetBridge.build_bake_pairs_manifest([src, tgt], "_high", "_low")
        self.assertEqual(pairs, {"pairs2_a": "source", "pairs2_b": "target"})

    def test_pairs_manifest_tags_group_children_by_default(self):
        """Name the GROUP root once: every mesh under it inherits the suffix.

        Real Maya DAG (not the mocked chain walk) -- this is what the
        SUFFIX_INCLUDE_CHILDREN default buys the user, and the sidecar is the
        only thing that carries it past Toolbag's transform-flattening import.
        """
        src = self._grouped_cube("pairs3_src_source", "pairs3_a")
        tgt = self._grouped_cube("pairs3_tgt", "pairs3_b")
        pairs = MarmosetBridge.build_bake_pairs_manifest([src, tgt], "_source", "")
        self.assertEqual(pairs, {"pairs3_a": "source"})

    def test_suffix_fallback_keys_are_registered_params(self):
        """Every key the panel greys must actually exist in the registry.

        ``set_param_enabled`` ignores unknown keys by design (a shared base
        may offer a row no panel registers), so a typo here would silently
        leave the row live instead of raising -- exactly the failure mode a
        greyed-out control is supposed to prevent.
        """
        from mayatk.mat_utils.marmoset_bridge.marmoset_bridge_slots import (
            MarmosetBridgeSlots,
        )

        for key in MarmosetBridgeSlots.SUFFIX_FALLBACK_KEYS:
            self.assertIn(key, _params.Parameters.PARAMS)

    def test_pairs_manifest_include_children_off_needs_own_suffix(self):
        """With the flag off, a suffixed group no longer adopts its meshes."""
        src = self._grouped_cube("pairs4_src_source", "pairs4_a")
        own = self._grouped_cube("pairs4_plain", "pairs4_b_source")
        pairs = MarmosetBridge.build_bake_pairs_manifest(
            [src, own], "_source", "", include_children=False
        )
        self.assertEqual(pairs, {"pairs4_b_source": "source"})

    def test_split_bake_objects_routes_high_set_members(self):
        from mayatk.mat_utils.bake_sets import BakeSourceSet

        src = self._grouped_cube("split_src", "split_src_mesh")
        tgt = self._grouped_cube("split_tgt", "split_tgt_mesh")
        BakeSourceSet.define([src])

        bridge = MarmosetBridge(toolbag_path="not-used.exe")
        lows, highs = bridge._split_bake_objects([tgt, src])
        self.assertEqual(lows, [tgt])
        self.assertEqual(highs, [src])

    def test_cage_measurement_warns_when_source_is_the_target_surface(self):
        """A UV re-layout send (source == target geometry) is not a high->low
        bake: the measurement sees a zero standoff and must say so, pointing
        at the UV Transfer tool, instead of letting the ray-cast bleed."""
        import logging

        src = self._grouped_cube("coinc_src", "coinc_mesh")
        tgt = self._grouped_cube("coinc_tgt", "coinc_mesh")  # identical cube
        bridge = MarmosetBridge(toolbag_path="not-used.exe")
        with self.assertLogs(bridge.logger, level=logging.WARNING) as logs:
            measured = bridge._cage_measurements([src], [tgt])
        self.assertIn("CAGE_STANDOFFS", measured)
        self.assertTrue(any("COINCIDENT" in m for m in logs.output))
        self.assertTrue(any("Transfer Textures" in m for m in logs.output))

    def test_cage_measurement_is_quiet_for_a_real_standoff(self):
        import logging

        src = self._grouped_cube("stand_src", "stand_mesh")
        tgt = self._grouped_cube("stand_tgt", "stand_mesh")
        cmds.scale(2.0, 2.0, 2.0, src)  # source stands 0.5 off the target cube
        bridge = MarmosetBridge(toolbag_path="not-used.exe")
        with self.assertNoLogs(bridge.logger, level=logging.WARNING):
            measured = bridge._cage_measurements([src], [tgt])
        self.assertGreater(max(measured["CAGE_STANDOFFS"].values()), 0.4)

    def test_bake_produce_exports_high_companion(self):
        """With the set defined, the bake export splits into two FBX files."""
        from mayatk.mat_utils.bake_sets import BakeSourceSet

        src = self._grouped_cube("comp_src", "comp_mesh")
        tgt = self._grouped_cube("comp_tgt", "comp_mesh")
        BakeSourceSet.define([src])

        out_dir = tempfile.mkdtemp(prefix="marmoset_test_")
        self.addCleanup(
            __import__("shutil").rmtree, out_dir, ignore_errors=True
        )
        with unittest.mock.patch(
            "mayatk.mat_utils.marmoset_bridge._marmoset_engine.AppLauncher.launch",
            return_value=None,
        ):
            bridge = MarmosetBridge(toolbag_path="not-used.exe")
            bridge.send(
                objects=[tgt],
                output_dir=out_dir,
                output_name="scene",
                template="bake",
                mode=SEND_TO,
            )

        self.assertTrue((Path(out_dir) / "scene.fbx").is_file())
        self.assertTrue(
            (Path(out_dir) / "scene_source.fbx").is_file(),
            "High Poly set must export as a companion FBX.",
        )
        self.assertFalse(
            (Path(out_dir) / "scene.bake_pairs.json").exists(),
            "Two-file mode needs no pairs sidecar.",
        )
        rendered = (Path(out_dir) / "scene_bake_send_to.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("scene_source.fbx", rendered)

    def test_manifest_includes_materials_from_group_selection(self):
        """A group root selection must resolve its descendants' materials."""
        from mayatk.mat_utils.mat_manifest import MatManifest

        grp = cmds.group(empty=True, name="manifest_grp")
        cube = cmds.polyCube(name="manifest_cube")[0]
        cube = cmds.parent(cube, grp)[0]
        shader = cmds.shadingNode(
            "standardSurface", asShader=True, name="M_ManifestGrp"
        )
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{shader}SG"
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(cube, edit=True, forceElement=sg)
        # A textured slot is required: textureless materials are (rightly)
        # dropped from the manifest, and this test is about hierarchy
        # expansion, not the empty-material filter.
        tex_dir = tempfile.mkdtemp(prefix="marmoset_test_")
        self.addCleanup(__import__("shutil").rmtree, tex_dir, ignore_errors=True)
        tex_path = (Path(tex_dir) / "grp_diffuse.png").as_posix()
        Path(tex_path).write_bytes(b"")
        file_node = cmds.shadingNode("file", asTexture=True, name="file_M_GrpBC")
        cmds.setAttr(f"{file_node}.fileTextureName", tex_path, type="string")
        cmds.connectAttr(f"{file_node}.outColor", f"{shader}.baseColor", force=True)

        manifest = MatManifest.build([cmds.ls(grp, long=True)[0]])
        self.assertIn(
            shader,
            manifest["materials"],
            "Group selection produced an empty manifest (get_mats reads "
            "direct shapes only; build must expand the hierarchy).",
        )

    def test_group_baked_outputs_buckets_by_set_name(self):
        outputs = [
            r"X:\out\scene_matA_Base_Color.png",
            r"X:\out\scene_matA_metal_Base_Color.png",
            r"X:\out\scene_matA_metal_AO.png",
            r"X:\out\scene_unrelated_AO.png",
        ]
        buckets = MarmosetBridge._group_baked_outputs(
            outputs, ["matA", "matA_metal"], strip_prefix="scene"
        )
        self.assertEqual(
            [os.path.basename(p) for p in buckets["matA"]],
            ["scene_matA_Base_Color.png"],
        )
        self.assertEqual(len(buckets["matA_metal"]), 2)

    def test_group_baked_outputs_single_material_takes_all(self):
        outputs = [r"X:\out\scene_Base_Color.png", r"X:\out\scene_AO.png"]
        buckets = MarmosetBridge._group_baked_outputs(outputs, ["only_mat"])
        self.assertEqual(buckets, {"only_mat": outputs})

    def test_group_baked_outputs_strip_prefix_guards_scene_name(self):
        """A scene name containing a material name must not swallow files."""
        outputs = [r"X:\out\matA_scene_matB_AO.png"]
        buckets = MarmosetBridge._group_baked_outputs(
            outputs, ["matA", "matB"], strip_prefix="matA_scene"
        )
        self.assertEqual(list(buckets), ["matB"])


if __name__ == "__main__":
    unittest.main()
