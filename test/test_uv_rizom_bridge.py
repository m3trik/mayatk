# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.uv_utils.rizom_bridge.

Regression coverage for the Maya-side of the bridge -- export path only.
RizomUV invocation (and every Lua preset) is exercised by the standalone
``test/rizom_headless_probe.py`` because it needs the external executable
-- run it after ANY edit to ``scripts/*.lua`` or ``templates/*.lua``.

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

from mayatk.uv_utils.rizom_bridge._rizom_bridge import RizomUVBridge, _SCRIPT_DIR
from mayatk.uv_utils.rizom_bridge import parameters as _params

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
        self.assertGreater(Path(self.export_path).stat().st_size, 0, "FBX is empty.")

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

        exported = [inside_long, outside_long] + extras
        # Should not raise.
        self.bridge._export_objects(exported)

        self.assertTrue(
            Path(self.export_path).exists(),
            f"FBX not written to {self.export_path}",
        )
        self.assertGreater(Path(self.export_path).stat().st_size, 0, "FBX is empty.")
        # Every exported object must land on its OWN map key -- colliding
        # leaf names used to collapse to one key, cross-wiring the UV
        # transfer (both SWITCH_GEOs would receive the same shell).
        self.assertEqual(
            len(self.bridge._export_name_map),
            len(exported),
            f"Expected one unique map key per export, got: "
            f"{self.bridge._export_name_map}",
        )
        # ... and on its own destination: both SWITCH_GEOs must be mapped
        # as distinct transfer targets.
        destinations = list(self.bridge._export_name_map.values())
        self.assertEqual(
            len(set(destinations)),
            len(exported),
            f"Transfer destinations are not unique: {destinations}",
        )
        switch_geos = [
            d for d in destinations if d.split("|")[-1].split(":")[-1] == "SWITCH_GEO"
        ]
        self.assertEqual(
            len(switch_geos),
            2,
            f"Both name-colliding originals should be mapped: {destinations}",
        )


class TestRizomBridgeLogic(MayaTkTestCase):
    """Pure-logic regressions: no export, no RizomUV run."""

    def setUp(self):
        super().setUp()
        self.bridge = RizomUVBridge(rizom_path="not-used.exe")

    def test_parse_rizom_version_handles_install_dir_variants(self):
        """The parser must survive every real-world install-dir naming."""
        cases = {
            r"C:\Program Files\Rizom Lab\RizomUV 2020.1\Rizomuv_VS.exe": (2020, 1),
            r"C:\Program Files\Rizom Lab\RizomUV VS RS 2022.2\rizomuv.exe": (2022, 2),
            r"C:\Program Files\Rizom Lab\RizomUV_2024\rizomuv.exe": (2024, 0),
            r"C:\Program Files\Rizom Lab\RizomUV VS RS 2023.10.1\rizomuv.exe": (
                2023,
                10,
                1,
            ),
            r"C:\tools\bin\someapp.exe": (0, 0),
        }
        for path, expected in cases.items():
            self.assertEqual(
                RizomUVBridge._parse_rizom_version(path),
                expected,
                f"Version mis-parsed for {path}",
            )

    def test_script_path_property_after_raw_content(self):
        """Setting raw Lua content must leave script_path readable.

        Regression: _prepare_script_file stored a plain str, so the
        script_path getter crashed on .as_posix().
        """
        self.bridge.script_path = 'ZomSelect({PrimType="Edge"})'
        resolved = self.bridge.script_path
        self.assertIsInstance(resolved, str)
        self.assertTrue(resolved.endswith(".lua"), resolved)
        self.assertTrue(Path(resolved).is_file(), resolved)

    def test_export_path_rejects_non_fbx(self):
        """OBJ was a trap -- the exporter always wrote FBX data."""
        with self.assertRaises(ValueError):
            self.bridge.export_path = "C:/temp/out.obj"

    def test_passthrough_script_still_substitutes_params(self):
        """A script with its own ZomLoad/ZomSave skips the wrapper but must
        still get version-stripping + placeholder substitution."""
        self.bridge._params = {"ITERATIONS": 25}
        raw = (
            'ZomLoad({File={Path="x.fbx"}})\n'
            "ZomUnfold({Iterations=__ITERATIONS__})\n"
            'ZomSave({File={Path="x.fbx"}})\n'
        )
        rendered = self.bridge._construct_full_script(raw)
        self.assertIn("Iterations=25", rendered)
        self.assertNotIn("__ITERATIONS__", rendered)
        # Not double-wrapped: exactly one ZomLoad.
        self.assertEqual(rendered.count("ZomLoad"), 1)

    def test_unwrap_presets_reference_expected_tokens(self):
        """Panel visibility is driven by the tokens each preset references."""
        hard = (_SCRIPT_DIR / "unwrap_hard.lua").read_text(encoding="utf-8")
        organic = (_SCRIPT_DIR / "unwrap_organic.lua").read_text(encoding="utf-8")

        hard_keys = _params.Parameters.referenced_keys(hard)
        organic_keys = _params.Parameters.referenced_keys(organic)

        self.assertIn("WELD_SEAMS", hard_keys)
        self.assertIn("SHARP_ANGLE", hard_keys)
        self.assertNotIn("DEVELOPABILITY", hard_keys)

        self.assertIn("WELD_SEAMS", organic_keys)
        self.assertIn("DEVELOPABILITY", organic_keys)
        # Organic segmentation no longer keys off dihedral angle.
        self.assertNotIn("SHARP_ANGLE", organic_keys)

    def test_param_labels_are_unique(self):
        """Two widgets with the same label are indistinguishable in the panel.

        Regression: MIX was labeled 'Mutations', colliding with
        PACK_MAX_MUTATIONS.
        """
        labels = [spec.label for spec in _params.PARAMS.values()]
        self.assertEqual(
            len(labels),
            len(set(labels)),
            f"Duplicate parameter labels: {[l for l in labels if labels.count(l) > 1]}",
        )

    def test_preset_min_version_parses_marker(self):
        """The @min_rizom header gates a preset as a whole."""
        self.assertEqual(
            _params.Parameters.preset_min_version("-- @min_rizom: 2022.2\nZomPack({})"),
            (2022, 2),
        )
        # Single-segment versions pad to (year, 0) for tuple comparison.
        self.assertEqual(
            _params.Parameters.preset_min_version("-- @min_rizom: 2024"),
            (2024, 0),
        )
        self.assertIsNone(_params.Parameters.preset_min_version("ZomPack({})"))
        # Shipped presets: pack is ungated, pack_into_existing needs 2022.2.
        pack = (_SCRIPT_DIR / "pack.lua").read_text(encoding="utf-8")
        gap = (_SCRIPT_DIR / "pack_into_existing.lua").read_text(encoding="utf-8")
        self.assertIsNone(_params.Parameters.preset_min_version(pack))
        self.assertEqual(_params.Parameters.preset_min_version(gap), (2022, 2))

    def test_pack_preset_references_placement_tokens(self):
        """pack.lua exposes the post-pack UDIM/coverage placement knobs;
        optimize.lua (layout-preserving) must not."""
        pack_keys = _params.Parameters.referenced_keys(
            (_SCRIPT_DIR / "pack.lua").read_text(encoding="utf-8")
        )
        optimize_keys = _params.Parameters.referenced_keys(
            (_SCRIPT_DIR / "optimize.lua").read_text(encoding="utf-8")
        )
        self.assertIn("TARGET_UDIM", pack_keys)
        self.assertIn("UV_AREA", pack_keys)
        self.assertNotIn("TARGET_UDIM", optimize_keys)
        self.assertNotIn("UV_AREA", optimize_keys)

    def test_gated_preset_refused_below_min_version(self):
        """pack_into_existing must fail loudly (not crash Rizom) on 2020.1."""
        bridge = RizomUVBridge(
            rizom_path=r"C:\Program Files\Rizom Lab\RizomUV 2020.1\Rizomuv_VS.exe"
        )
        cube = cmds.polyCube(name="gateCube")[0]
        with self.assertRaisesRegex(RuntimeError, "requires RizomUV >= 2022.2"):
            bridge.process_with_rizomuv(
                [cube], preset="pack_into_existing", select_objects=[cube]
            )

    def test_selection_preset_requires_select_objects(self):
        """A script with the selection token refuses to run without
        select_objects -- the raw token would be a Lua syntax error."""
        cube = cmds.polyCube(name="selReqCube")[0]
        script = (
            "ZomSelect({Names=__PACK_SELECT_NAMES__, Select=true})\n"  # noqa: P103
        )
        with self.assertRaisesRegex(ValueError, "select_objects"):
            self.bridge.process_with_rizomuv([cube], uv_script=script)

    def test_expand_includes_expands_directive_not_comment(self):
        """__PACK_BLOCK__ expands only as a standalone directive line -- an
        in-comment mention stays literal (the expander is a blind replace
        for everything else and must not clobber comments)."""
        directive = _params.Parameters.expand_includes("x\n__PACK_BLOCK__\ny")
        self.assertIn("ZomPack", directive)
        self.assertNotIn("__PACK_BLOCK__", directive)

        commented = _params.Parameters.expand_includes(
            "-- see __PACK_BLOCK__ for details\ncode"
        )
        self.assertNotIn("ZomPack", commented)
        self.assertIn("__PACK_BLOCK__", commented)  # untouched

    def test_pack_block_shared_by_pack_and_unwrap_presets(self):
        """pack / unwrap_hard / unwrap_organic all pull the shared block, so
        the pack knobs are defined once. optimize keeps its own inline block."""
        for preset in ("pack", "unwrap_hard", "unwrap_organic"):
            body = (_SCRIPT_DIR / f"{preset}.lua").read_text(encoding="utf-8")
            self.assertIn("__PACK_BLOCK__", body, f"{preset} should use the shared block")
        optimize = (_SCRIPT_DIR / "optimize.lua").read_text(encoding="utf-8")
        self.assertNotIn("__PACK_BLOCK__", optimize)
        self.assertIn("ZomPack", optimize)  # its own inline pack

    def test_padding_field_name_gated_by_rizom_version(self):
        """Island spacing renders as SpacingSize on <= 2021 (2020.1-safe) and
        PaddingSize on >= 2022 (the probed rename); MarginSize always survives."""
        pack = _params.Parameters.expand_includes(
            (_SCRIPT_DIR / "pack.lua").read_text(encoding="utf-8")
        )
        old = _params.Parameters.strip_unsupported(pack, (2020, 1))
        new = _params.Parameters.strip_unsupported(pack, (2022, 0))
        self.assertIn("SpacingSize=", old)
        self.assertNotIn("PaddingSize=", old)
        self.assertIn("PaddingSize=", new)
        self.assertNotIn("SpacingSize=", new)
        self.assertIn("MarginSize=", old)
        self.assertIn("MarginSize=", new)

    def test_hard_reweld_unoverlap_gated_2022(self):
        """ReWeld / BooleanUnoverlap access-violate 2020.1 (probed) -- stripped
        below 2022, present at/above it."""
        hard = _params.Parameters.expand_includes(
            (_SCRIPT_DIR / "unwrap_hard.lua").read_text(encoding="utf-8")
        )
        old = _params.Parameters.strip_unsupported(hard, (2020, 1))
        new = _params.Parameters.strip_unsupported(hard, (2022, 0))
        for field in ("ReWeld=", "BooleanUnoverlap="):
            self.assertNotIn(field, old)
            self.assertIn(field, new)

    def test_new_params_registered(self):
        """Phase 1/2 knobs exist with the expected kinds; the scaling enums
        now offer the scale-preservation values."""
        self.assertEqual(_params.PARAMS["PACK_SPACING"].kind, "float")
        self.assertEqual(_params.PARAMS["PACK_MARGIN"].kind, "float")
        self.assertEqual(_params.PARAMS["FIT_CONES"].kind, "bool")
        scale_labels = [c[0] for c in _params.PARAMS["SCALING_MODE"].choices]
        self.assertTrue(any("Keep current scale" in l for l in scale_labels))
        layout_vals = [c[1] for c in _params.PARAMS["LAYOUT_SCALING_MODE"].choices]
        self.assertIn(0, layout_vals)  # "Keep positions" for scale preservation

    def test_organic_exposes_fitcones_hybrid_gated(self):
        """FIT_CONES is a live organic knob; unwrap_hybrid is preset-gated 2022."""
        organic_keys = _params.Parameters.referenced_keys(
            (_SCRIPT_DIR / "unwrap_organic.lua").read_text(encoding="utf-8")
        )
        self.assertIn("FIT_CONES", organic_keys)
        hybrid = (_SCRIPT_DIR / "unwrap_hybrid.lua").read_text(encoding="utf-8")
        self.assertEqual(_params.Parameters.preset_min_version(hybrid), (2022, 0))


class TestRizomBridgePackIntoExisting(MayaTkTestCase):
    """Maya-side plumbing for the pack_into_existing flow (no RizomUV run)."""

    def test_select_names_lua_maps_to_exported_names(self):
        """select_objects resolve to the suffixed FBX group names."""
        bridge = RizomUVBridge(rizom_path="not-used.exe")
        a = cmds.polyCube(name="existingMesh")[0]
        b = cmds.polyCube(name="newMesh")[0]
        bridge.export_path = str(Path(tempfile.gettempdir()) / "riz_sel_test.fbx")
        bridge._export_objects([a, b])

        lua = bridge._select_names_lua([b])
        # Index is export-order-dependent (an implementation detail) --
        # assert the shape, not the counter.
        self.assertRegex(lua, r'^\{"newMesh_\d+__RZTMP"\}$')
        self.assertNotIn("existingMesh", lua)

        # Objects outside the export set fail loudly.
        c = cmds.polyCube(name="unexported")[0]
        with self.assertRaises(ValueError):
            bridge._select_names_lua([c])

    def test_expand_by_materials_pulls_material_sharers(self):
        """Expansion = every mesh sharing the selection's material(s)."""
        a = cmds.polyCube(name="expandNew")[0]
        b = cmds.polyCube(name="expandExisting")[0]
        c = cmds.polyCube(name="expandUnrelated")[0]

        mat = cmds.shadingNode("lambert", asShader=True, name="expandMat")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="expandMatSG"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets([a, b], edit=True, forceElement=sg)

        all_objs, selected = RizomUVBridge.expand_by_materials([a])
        all_leaves = {n.rsplit("|", 1)[-1] for n in all_objs}
        sel_leaves = {n.rsplit("|", 1)[-1] for n in selected}

        self.assertEqual(sel_leaves, {"expandNew"})
        self.assertIn("expandExisting", all_leaves)
        self.assertIn("expandNew", all_leaves)
        self.assertNotIn("expandUnrelated", all_leaves)

    def test_skip_instances_exports_one_rep_per_shared_shape(self):
        """True DAG instances (shared shape) collapse to one exported rep;
        the non-instance sibling still exports."""
        base = cmds.polyCube(name="instBase")[0]
        # cmds.instance makes a second transform over the SAME shape.
        inst = cmds.instance(base, name="instCopy")[0]
        solo = cmds.polyCube(name="instSolo")[0]

        bridge = RizomUVBridge(rizom_path="not-used.exe")
        bridge.export_path = str(Path(tempfile.gettempdir()) / "riz_inst_test.fbx")

        # filter_duplicate_instances (the dedupe the bridge applies) keeps one
        # of the shared-shape pair plus the solo cube.
        from mayatk.node_utils._node_utils import NodeUtils

        kept = NodeUtils.filter_duplicate_instances([base, inst, solo])
        kept_leaves = {str(k).rsplit("|", 1)[-1] for k in kept}
        self.assertEqual(len(kept), 2, kept)
        self.assertIn("instSolo", kept_leaves)
        self.assertTrue({"instBase", "instCopy"} & kept_leaves)


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

        cmb = ui.cmb000
        items_by_text = {cmb.itemText(i): i for i in range(cmb.count())}

        # Only presets actually offered in the combo are selectable -- version-
        # gated presets (unwrap_hybrid, pack_into_existing below their gate)
        # are absent, so compare among what the combo lists, not the file glob.
        scripts = [s for s in items_by_text if (bridge_mod._SCRIPT_DIR / f"{s}.lua").is_file()]

        def row_count(stem):
            path = bridge_mod._SCRIPT_DIR / f"{stem}.lua"
            return len(
                _params.Parameters.referenced_keys(path.read_text(encoding="utf-8"))
            )

        if len(scripts) < 2:
            self.skipTest("Need at least two selectable scripts to compare heights.")
        sorted_by_rows = sorted(scripts, key=row_count)
        few, many = sorted_by_rows[0], sorted_by_rows[-1]
        if row_count(few) == row_count(many):
            self.skipTest("All selectable scripts reference the same param count.")

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
            self._launch_calls.append(
                {
                    "app": app_identifier,
                    "args": list(args or []),
                    "detached": detached,
                }
            )

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
            first_script,
            second_script,
            "Lua script path must differ between sends so prior Rizom "
            "sessions aren't re-triggered via the -cfi mtime watch.",
        )
        self.assertEqual(
            len(second_fbxs),
            2,
            f"expected 2 fbx files after 2 sends (one per send), got {second_fbxs}",
        )

    def test_send_script_inlines_load_options_and_texture(self):
        """Param overrides + textures from the shading network reach the Lua."""
        cube = cmds.polyCube(name="rizom_send_inline_cube")[0]

        # Build a minimal shading network with a file texture so
        # MatUtils.get_texture_paths finds something.
        shader = cmds.shadingNode("lambert", asShader=True, name="rizom_send_lam_t")
        sg = cmds.sets(
            renderable=True,
            noSurfaceShader=True,
            empty=True,
            name="rizom_send_lamSG_t",
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        fn = cmds.shadingNode("file", asTexture=True, name="rizom_send_file_t")

        # The texture file must EXIST on disk -- _collect_texture_loads
        # filters out missing paths (Fix #5 in this commit batch).
        tex_path = self.tmp_dir / "diffuse.png"
        # Minimum-valid 1x1 PNG so we don't need PIL in the test env.
        import base64

        tex_path.write_bytes(
            base64.b64decode(
                b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADklEQVR42mP8z8BQDwAEhQGAh"
                b"KmMIQAAAABJRU5ErkJggg=="
            )
        )
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
        self.assertIn(
            "ImportGroups=false", body, "IMPORT_GROUPS=False did not propagate."
        )
        self.assertIn(
            "ZomLoadTexture",
            body,
            "Texture from shading network did not reach the Lua script.",
        )
        # No ZomSave / ZomQuit *calls*: send is one-way, Rizom must stay
        # open. The wrapper's leading comment block mentions these names
        # as documentation -- strip comments first so the substring check
        # only looks at executable Lua.
        executable = "\n".join(
            line for line in body.splitlines() if not line.lstrip().startswith("--")
        )
        self.assertNotIn(
            "ZomSave(", executable, f"send body must not save: {executable}"
        )
        self.assertNotIn(
            "ZomQuit(", executable, f"send body must not quit: {executable}"
        )

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
            renderable=True,
            noSurfaceShader=True,
            empty=True,
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
            "ZomLoadTexture",
            body,
            "Missing texture file should be filtered out, not passed to Rizom.",
        )


if __name__ == "__main__":
    unittest.main()
