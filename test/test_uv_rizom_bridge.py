# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.uv_utils.rizom_bridge.

Regression coverage for the Maya-side of the bridge -- export path only.
RizomUV invocation is exercised by the standalone smoketest under
``temp_tests/`` because it needs the external executable.

Tests here run inside a live Maya session via ``run_tests.py`` and catch
the failure modes the standalone smoketest cannot:

- ``fbxmaya`` plugin not pre-loaded in interactive Maya.
- Multiple duplicates collapsing to the same leaf name (different parents)
  causing ``cmds.select`` ambiguity.
"""
import os
import unittest
import tempfile
from pathlib import Path

import maya.cmds as cmds

from mayatk.uv_utils.rizom_bridge._rizom_bridge import RizomUVBridge

from base_test import MayaTkTestCase


class TestRizomBridgeExport(MayaTkTestCase):
    """Maya-only: validates the export half of the bridge end-to-end."""

    def setUp(self):
        super().setUp()
        # Force the bridge to a temp path each test so we can assert on it.
        fd, path = tempfile.mkstemp(suffix=".fbx", prefix="rizom_test_")
        os.close(fd)
        # The file must NOT exist when the export runs (mtime check is permissive
        # but we only care about the post-state here).
        Path(path).unlink(missing_ok=True)
        self.export_path = path

        # Construct a bridge but do not require RizomUV on disk -- we never
        # invoke the executable from these tests.
        self.bridge = RizomUVBridge(rizom_path="not-used.exe")
        self.bridge.export_path = self.export_path

    def tearDown(self):
        Path(self.export_path).unlink(missing_ok=True)
        super().tearDown()

    def test_export_loads_fbx_plugin_when_unloaded(self):
        """Bridge must load fbxmaya itself; live Maya doesn't pre-load it."""
        if cmds.pluginInfo("fbxmaya", query=True, loaded=True):
            try:
                cmds.unloadPlugin("fbxmaya", force=True)
            except RuntimeError:
                self.skipTest("fbxmaya cannot be unloaded in this session.")

        cube = cmds.polyCube(name="rizom_plugin_test")[0]
        self.bridge._export_objects([cube])

        self.assertTrue(
            cmds.pluginInfo("fbxmaya", query=True, loaded=True),
            "Bridge should have loaded fbxmaya before exporting.",
        )
        self.assertTrue(
            Path(self.export_path).exists(),
            f"FBX not written to {self.export_path}",
        )
        self.assertGreater(
            Path(self.export_path).stat().st_size, 0, "FBX is empty."
        )

    def test_export_handles_name_collisions_under_different_parents(self):
        """Two duplicates may share a leaf name -- bridge must use long paths.

        Reproduces the 29-object failure: when ``cmds.duplicate`` produces
        nodes whose post-rename leaf names collide (e.g. one at world root
        and one under another parent), ``cmds.select`` raises
        'More than one object matches name'. The bridge must resolve to
        full DAG paths before selecting.
        """
        # Parent group whose child collides with a world-root sibling.
        parent = cmds.group(empty=True, name="OUTPUT_CTRL")
        inside = cmds.polyCube(name="SWITCH_GEO")[0]
        cmds.parent(inside, parent)
        # Need its long path for export -- short name "SWITCH_GEO" exists twice.
        inside_long = cmds.ls(inside, long=True)[0]

        outside = cmds.polyCube(name="SWITCH_GEO")[0]  # world root, same leaf
        outside_long = cmds.ls(outside, long=True)[0]

        # Add a few unrelated cubes so the test mirrors the bulk-export shape.
        extras = [cmds.polyCube(name=f"extra_{i}")[0] for i in range(5)]

        # Should not raise.
        self.bridge._export_objects([inside_long, outside_long] + extras)

        self.assertTrue(
            Path(self.export_path).exists(),
            f"FBX not written to {self.export_path}",
        )
        self.assertGreater(
            Path(self.export_path).stat().st_size, 0, "FBX is empty."
        )


class TestRizomBridgeUiResize(MayaTkTestCase):
    """The window must shrink/grow when the active script's parameters change."""

    def test_window_height_tracks_visible_param_rows(self):
        """Switching scripts hides/shows rows and the window follows."""
        from qtpy import QtWidgets
        from uitk import Switchboard
        from mayatk.uv_utils.rizom_bridge.rizom_bridge_slots import (
            RizomBridgeSlots,
        )
        from mayatk.uv_utils.rizom_bridge import _rizom_bridge as bridge_mod
        from mayatk.uv_utils.rizom_bridge import parameters as _params

        sb = Switchboard(
            ui_source=str(bridge_mod._PKG_DIR),
            slot_source=RizomBridgeSlots,
        )
        ui = sb.loaded_ui.rizom_bridge
        # Don't load (or persist) saved geometry -- we want a controlled height.
        ui.restore_window_size = False
        ui.show()
        QtWidgets.QApplication.processEvents()
        ui.is_initialized = True

        scripts = sorted(p.stem for p in bridge_mod._SCRIPT_DIR.glob("*.lua"))

        def row_count(stem):
            path = bridge_mod._SCRIPT_DIR / f"{stem}.lua"
            return len(_params.referenced_keys(path.read_text(encoding="utf-8")))

        if len(scripts) < 2:
            self.skipTest("Need at least two bundled scripts to compare heights.")
        sorted_by_rows = sorted(scripts, key=row_count)
        few, many = sorted_by_rows[0], sorted_by_rows[-1]
        if row_count(few) == row_count(many):
            self.skipTest("All bundled scripts reference the same param count.")

        cmb = ui.cmb000
        items_by_text = {cmb.itemText(i): i for i in range(cmb.count())}

        # Start with the wider preset and force the window taller than
        # whatever fit would compute, so we can observe a shrink delta.
        cmb.setCurrentIndex(items_by_text[many])
        QtWidgets.QApplication.processEvents()
        ui.resize(ui.width(), 800)
        QtWidgets.QApplication.processEvents()
        height_many = ui.height()

        cmb.setCurrentIndex(items_by_text[few])
        # Drain the event queue enough for the deferred fit (QTimer.singleShot)
        # to fire AND its resize() to settle.
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


class TestRizomBridgeSendFlow(MayaTkTestCase):
    """One-way ``send_to_rizomuv`` flow: export + Lua render + detached launch.

    Stubs out the actual RizomUV launch so the test exercises the bridge
    end-to-end (selection, export, script render, texture collection,
    launch invocation) without needing the external executable.
    """

    def setUp(self):
        super().setUp()
        # The bridge resolves rizom_path via AppLauncher; pass an explicit
        # bogus value so the .rizom_path property short-circuits.
        self.bridge = RizomUVBridge(rizom_path="rizom-stub.exe")

        # Force a temp export dir each test so the unique-per-send paths
        # land in a known sandbox and we can assert on them.
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="rizom_send_test_"))
        # The export_path property is used to derive the per-send FBX dir;
        # setting a stem here biases the per-send filename for visibility
        # in failure messages.
        self.bridge.export_path = str(self.tmp_dir / "scene.fbx")

        # Capture every AppLauncher.launch call so the test can assert on
        # the args without spawning a real process.
        from pythontk.core_utils import app_launcher as _al
        self._launch_calls = []

        def _fake_launch(app_identifier, args=None, cwd=None, detached=True, env=None):
            self._launch_calls.append({
                "app": app_identifier,
                "args": list(args or []),
                "detached": detached,
            })
            class _Proc:
                pid = 0
            return _Proc()

        self._real_launch = _al.AppLauncher.launch
        _al.AppLauncher.launch = staticmethod(_fake_launch)

    def tearDown(self):
        from pythontk.core_utils import app_launcher as _al
        _al.AppLauncher.launch = self._real_launch
        # Best-effort: drop the test sandbox; ignore stragglers because
        # Rizom's mtime watch can hold a handle briefly on real hardware.
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        super().tearDown()

    def test_send_writes_unique_fbx_and_script_per_call(self):
        """Two consecutive sends must land on distinct FBX + Lua paths.

        Regression: Rizom 2020.1's ``-cfi`` flag watches the script file's
        mtime and re-executes whenever it changes. If both sends wrote to
        the same Lua path, the first send's still-open Rizom session
        would reload the second send's mesh, clobbering any unsaved UV
        work. Each send must land on its own files.
        """
        cube = cmds.polyCube(name="rizom_send_unique_cube")[0]

        self.bridge.send_to_rizomuv([cube])
        first = self._launch_calls[-1]["args"]
        # Args shape: ['-cfi', '<script-path>']
        self.assertEqual(first[0], "-cfi", f"unexpected launch args: {first}")
        first_script = first[1]
        # Discover the FBX path the first send wrote (the only *.fbx
        # under the sandbox so far).
        first_fbxs = list(self.tmp_dir.glob("*.fbx"))
        self.assertEqual(
            len(first_fbxs), 1, f"expected 1 fbx after first send, got {first_fbxs}"
        )

        self.bridge.send_to_rizomuv([cube])
        second = self._launch_calls[-1]["args"]
        second_script = second[1]
        second_fbxs = sorted(self.tmp_dir.glob("*.fbx"))

        self.assertNotEqual(
            first_script, second_script,
            "Lua script path must differ between sends so prior Rizom "
            "sessions aren't re-triggered via the -cfi mtime watch.",
        )
        self.assertEqual(
            len(second_fbxs), 2,
            f"expected 2 fbx files after 2 sends (one per send), got {second_fbxs}",
        )

    def test_send_script_inlines_load_options_and_texture(self):
        """Param overrides + textures from the shading network reach the Lua."""
        cube = cmds.polyCube(name="rizom_send_inline_cube")[0]

        # Build a minimal shading network with a file texture so
        # MatUtils.get_texture_paths finds something.
        shader = cmds.shadingNode("lambert", asShader=True, name="rizom_send_lam_t")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True,
            name="rizom_send_lamSG_t",
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        fn = cmds.shadingNode("file", asTexture=True, name="rizom_send_file_t")

        # The texture file must EXIST on disk -- _collect_texture_loads
        # filters out missing paths (Fix #5 in this commit batch).
        tex_path = self.tmp_dir / "diffuse.png"
        # Minimum-valid 1x1 PNG so we don't need PIL in the test env.
        import base64
        tex_path.write_bytes(base64.b64decode(
            b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADklEQVR42mP8z8BQDwAEhQGAh"
            b"KmMIQAAAABJRU5ErkJggg=="
        ))
        cmds.setAttr(f"{fn}.fileTextureName", str(tex_path), type="string")
        cmds.connectAttr(f"{fn}.outColor", f"{shader}.color", force=True)
        cmds.sets(cube, edit=True, forceElement=sg)

        self.bridge.send_to_rizomuv(
            [cube],
            params={
                "LOAD_UVS": False,
                "LOAD_UVW_PROPS": True,
                "IMPORT_GROUPS": False,
                "LOAD_TEXTURES": True,
            },
        )

        script_path = self._launch_calls[-1]["args"][1]
        body = Path(script_path).read_text(encoding="utf-8")

        self.assertIn("XYZUVW=false", body, "LOAD_UVS=False did not propagate.")
        self.assertIn("UVWProps=true", body, "LOAD_UVW_PROPS=True did not propagate.")
        self.assertIn("ImportGroups=false", body, "IMPORT_GROUPS=False did not propagate.")
        self.assertIn(
            "ZomLoadTexture", body,
            "Texture from shading network did not reach the Lua script.",
        )
        # No ZomSave / ZomQuit *calls*: send is one-way, Rizom must stay
        # open. The wrapper's leading comment block mentions these names
        # as documentation -- strip comments first so the substring check
        # only looks at executable Lua.
        executable = "\n".join(
            line for line in body.splitlines() if not line.lstrip().startswith("--")
        )
        self.assertNotIn("ZomSave(", executable, f"send body must not save: {executable}")
        self.assertNotIn("ZomQuit(", executable, f"send body must not quit: {executable}")

    def test_send_skips_missing_texture_files(self):
        """A ``fileTextureName`` pointing at a non-existent file is dropped.

        Regression for the silent-pcall failure: if we emit
        ``ZomLoadTexture`` for a missing file, Rizom's pcall catches it
        and the user sees no texture on the model with no explanation.
        Filter at the bridge level instead.
        """
        cube = cmds.polyCube(name="rizom_send_skip_cube")[0]
        shader = cmds.shadingNode("lambert", asShader=True, name="rizom_send_lam_s")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True,
            name="rizom_send_lamSG_s",
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        fn = cmds.shadingNode("file", asTexture=True, name="rizom_send_file_s")
        cmds.setAttr(
            f"{fn}.fileTextureName",
            str(self.tmp_dir / "does_not_exist.png"),
            type="string",
        )
        cmds.connectAttr(f"{fn}.outColor", f"{shader}.color", force=True)
        cmds.sets(cube, edit=True, forceElement=sg)

        self.bridge.send_to_rizomuv([cube], params={"LOAD_TEXTURES": True})

        script_path = self._launch_calls[-1]["args"][1]
        body = Path(script_path).read_text(encoding="utf-8")
        self.assertNotIn(
            "ZomLoadTexture", body,
            "Missing texture file should be filtered out, not passed to Rizom.",
        )


if __name__ == "__main__":
    unittest.main()
