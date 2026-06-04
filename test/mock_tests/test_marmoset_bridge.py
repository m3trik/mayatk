import sys
from unittest.mock import MagicMock

# Detect whether real maya.cmds is already loaded (run_tests.py path).
# If so, skip mocking entirely -- mocks would corrupt sys.modules and break
# imports of production modules that need the real Maya runtime.
_REAL_MAYA_AVAILABLE = "maya.cmds" in sys.modules and not isinstance(
    sys.modules.get("maya.cmds"), MagicMock
)

if _REAL_MAYA_AVAILABLE:
    mock_cmds = sys.modules["maya.cmds"]
else:
    mock_cmds = sys.modules.get("maya.cmds")
    if not isinstance(mock_cmds, MagicMock):
        mock_maya = MagicMock()
        mock_maya.__name__ = "maya"
        mock_cmds = MagicMock()
        mock_cmds.__name__ = "maya.cmds"
        sys.modules["maya"] = mock_maya
        sys.modules["maya.cmds"] = mock_cmds
        mock_maya.cmds = mock_cmds
        for _name in ("maya.mel", "maya.api", "maya.api.OpenMaya", "maya.OpenMaya"):
            _m = MagicMock()
            _m.__name__ = _name
            sys.modules[_name] = _m

    mock_cmds.ls.return_value = []

import unittest
import unittest.mock
import ast
import os
import tempfile

from mayatk.mat_utils.mat_manifest import MatManifest
from mayatk.mat_utils.marmoset_bridge._marmoset_bridge import (
    MarmosetBridge,
    SEND_TO,
    ROUNDTRIP,
    _TEMPLATE_DIR,
    list_templates,
    list_template_modes,
    template_modes,
    build_bake_pairs_manifest,
)
# Log helpers are bundled in the marmoset_bridge subpackage alongside the engine.
from mayatk.mat_utils.marmoset_bridge.toolbag_log import (
    resolve_toolbag_log_path,
    classify_log_line,
    dispatch_log_lines,
    start_toolbag_log_tail,
)
from mayatk.mat_utils.marmoset_bridge import parameters as _params


_CMDS_IS_MOCKED = not _REAL_MAYA_AVAILABLE


@unittest.skipUnless(
    _CMDS_IS_MOCKED, "Mock-based test -- run via pytest, not run_tests.py"
)
class TestMarmosetBridgeStandalone(unittest.TestCase):
    def setUp(self):
        mock_cmds.reset_mock()

    # ------------------------------------------------------------------
    # Manifest (unchanged from the prior suite)
    # ------------------------------------------------------------------

    def test_mat_manifest_structure(self):
        """MatManifest produces materials -> baseColor=path for a standardSurface."""
        mock_obj = MagicMock()
        mock_obj.name.return_value = "pCube1"
        mock_shader_name = "M_Standard"

        with unittest.mock.patch(
            "mayatk.mat_utils._mat_utils.MatUtils.get_mats",
            return_value=[mock_shader_name],
        ):
            mock_cmds.nodeType.return_value = "standardSurface"

            def side_effect_get_tex(mat, attr):
                if mat == mock_shader_name and attr == "baseColor":
                    return "fileNode1"
                return None

            with unittest.mock.patch(
                "mayatk.mat_utils._mat_utils.MatUtils.get_texture_file_node",
                side_effect=side_effect_get_tex,
            ):
                with unittest.mock.patch(
                    "mayatk.mat_utils._mat_utils.MatUtils._paths_from_file_nodes",
                    return_value=["C:/textures/diffuse.png"],
                ):
                    manifest = MatManifest.build([mock_obj])

                    self.assertIn("materials", manifest)
                    self.assertIn(mock_shader_name, manifest["materials"])
                    self.assertEqual(
                        manifest["materials"][mock_shader_name].get("baseColor"),
                        "C:/textures/diffuse.png",
                    )

    def test_manifest_builder_map_consistency(self):
        """Unknown shader types are skipped silently."""
        mock_obj = MagicMock()
        mock_shader_name = "M_Weird"

        with unittest.mock.patch(
            "mayatk.mat_utils._mat_utils.MatUtils.get_mats",
            return_value=[mock_shader_name],
        ):
            mock_cmds.nodeType.return_value = "unknownShader_type_xyz"
            manifest = MatManifest.build([mock_obj])

            self.assertIn("materials", manifest)
            self.assertNotIn(mock_shader_name, manifest["materials"])

    # ------------------------------------------------------------------
    # send() pipeline
    # ------------------------------------------------------------------

    def test_send_to_writes_fbx_manifest_and_script(self):
        """send_to mode: exports FBX, writes manifest, produces parseable script."""
        with unittest.mock.patch(
            "mayatk.mat_utils.marmoset_bridge._marmoset_bridge.FbxUtils"
        ) as mock_fbx:
            with unittest.mock.patch(
                "mayatk.mat_utils.marmoset_bridge._marmoset_bridge.MatManifest"
            ) as mock_builder:
                mock_builder.build.return_value = {"materials": {}}

                # The engine verifies the exported model exists before it
                # renders, so the mocked export must actually drop a file.
                def _fake_export(**kwargs):
                    with open(kwargs["file_path"], "w", encoding="utf-8") as fh:
                        fh.write("")

                mock_fbx.export.side_effect = _fake_export

                # Launch now happens in the engine; patch AppLauncher there
                # so send_to doesn't spawn a real Toolbag.
                with unittest.mock.patch(
                    "mayatk.mat_utils.marmoset_bridge._marmoset_engine.AppLauncher"
                ):
                    output_dir = tempfile.mkdtemp(prefix="marmoset_test_")
                    bridge = MarmosetBridge()
                    result = bridge.send(
                        objects=["pCube1"],
                        output_dir=output_dir,
                        output_name="unit",
                        template="bake",
                        mode=SEND_TO,
                        toolbag_exe="fake_toolbag.exe",
                    )

                    self.assertIsNotNone(result, "send() returned None unexpectedly")
                    self.assertEqual(result["mode"], SEND_TO)
                    self.assertNotIn(
                        "outputs", result, "send_to should not produce 'outputs'"
                    )

                    self.assertTrue(mock_fbx.export.called)
                    fbx_kwargs = mock_fbx.export.call_args.kwargs
                    self.assertTrue(fbx_kwargs["file_path"].endswith("unit.fbx"))

                    manifest_path = os.path.join(output_dir, "unit.materials.json")
                    self.assertTrue(os.path.isfile(manifest_path))

                    # Script path now embeds the mode for traceability.
                    script_path = os.path.join(output_dir, "unit_bake_send_to.py")
                    self.assertTrue(os.path.isfile(script_path))
                    with open(script_path, "r", encoding="utf-8") as fh:
                        body = fh.read()
                    ast.parse(body)

                    # send_to => not headless => SHOULD_QUIT should be False.
                    self.assertIn("SHOULD_QUIT = False", body)

                    for key in _params.PARAMS:
                        self.assertNotIn(
                            f"__{key}__",
                            body,
                            f"Placeholder __{key}__ was not substituted in bake.py",
                        )
                    for fixed in (
                        "__MODEL_PATH__",
                        "__MANIFEST_PATH__",
                        "__PAIRS_PATH__",
                        "__OUTPUT_DIR__",
                        "__SAVE_PATH__",
                        "__SHOULD_QUIT__",
                        "__TOOLBAG_HELPERS_DIR__",
                    ):
                        self.assertNotIn(fixed, body, f"{fixed} not substituted")

                    # Empty-pairs short-circuit: pCube1 has no parent chain
                    # suffix in this mock, so the bridge must NOT pollute the
                    # output directory with a no-op ``{}`` sidecar.
                    pairs_path = os.path.join(output_dir, "unit.bake_pairs.json")
                    self.assertFalse(
                        os.path.isfile(pairs_path),
                        "bake_pairs.json should not be written when there's "
                        "nothing to classify",
                    )

    def test_send_rejects_mode_not_in_template_BRIDGE_MODES(self):
        """A template that declares only send_to cannot be invoked roundtrip."""
        with unittest.mock.patch(
            "mayatk.mat_utils.marmoset_bridge._marmoset_bridge.FbxUtils"
        ):
            bridge = MarmosetBridge()
            # 'import' template declares BRIDGE_MODES = ('send_to',)
            result = bridge.send(
                objects=["pCube1"],
                output_dir=tempfile.mkdtemp(prefix="marmoset_test_reject_"),
                output_name="unit",
                template="import",
                mode=ROUNDTRIP,
            )
            self.assertIsNone(result, "Roundtrip on send_to-only template must fail")

    # ------------------------------------------------------------------
    # Template & parameter registry
    # ------------------------------------------------------------------

    def test_every_bundled_template_renders_and_parses(self):
        """Each bundled template, rendered with defaults, must parse as Python."""
        templates = sorted(p.stem for p in list_templates())
        self.assertTrue(templates, "No bundled templates found.")

        bridge = MarmosetBridge()
        for stem in templates:
            with self.subTest(template=stem):
                rendered = bridge.render_template(
                    template=stem,
                    model_path="/tmp/a.fbx",
                    manifest_path="/tmp/a.materials.json",
                    output_dir="/tmp/out",
                    headless=False,
                )
                self.assertIsNotNone(rendered, f"Template {stem} did not render.")
                try:
                    ast.parse(rendered)
                except SyntaxError as e:
                    self.fail(f"Template {stem} produced invalid Python: {e}")

    def test_render_template_overrides_apply(self):
        """User-supplied params override registry defaults in the rendered body."""
        bridge = MarmosetBridge()
        rendered = bridge.render_template(
            template="bake",
            model_path="/tmp/a.fbx",
            manifest_path="/tmp/a.materials.json",
            output_dir="/tmp/out",
            headless=False,
            params={"BAKE_SIZE": 4096, "BAKE_BITS": 16, "MAP_NORMAL": False},
        )
        self.assertIn("BAKE_SIZE = 4096", rendered)
        self.assertIn("BAKE_BITS = 16", rendered)
        self.assertIn("MAP_NORMAL = False", rendered)

    def test_render_template_unknown_name_returns_none(self):
        """Unknown template name surfaces a None return, not an exception."""
        bridge = MarmosetBridge()
        self.assertIsNone(
            bridge.render_template(
                template="does_not_exist",
                model_path="/tmp/a.fbx",
                manifest_path="/tmp/a.materials.json",
                output_dir="/tmp/out",
            )
        )

    # ------------------------------------------------------------------
    # BRIDGE_MODES parsing
    # ------------------------------------------------------------------

    def test_bridge_modes_per_template(self):
        """Each bundled template declares the modes we expect."""
        modes = {p.stem: template_modes(p) for p in list_templates()}
        self.assertEqual(modes.get("import"), (SEND_TO,))
        self.assertEqual(modes.get("lookdev"), (SEND_TO,))
        # bake supports both -- order matters: it's the source of truth for
        # the combo's expansion.
        self.assertEqual(modes.get("bake"), (SEND_TO, ROUNDTRIP))

    def test_list_template_modes_expands_dual_mode(self):
        """list_template_modes() yields one (stem, mode) per declared mode."""
        pairs = list_template_modes()
        self.assertIn(("import", SEND_TO), pairs)
        self.assertIn(("lookdev", SEND_TO), pairs)
        self.assertIn(("bake", SEND_TO), pairs)
        self.assertIn(("bake", ROUNDTRIP), pairs)
        # 'bake' should be present twice -- once per mode.
        bake_count = sum(1 for t, _m in pairs if t == "bake")
        self.assertEqual(bake_count, 2)

    def test_render_template_mode_drives_headless(self):
        """render_template(mode=roundtrip) implies headless; send_to does not."""
        bridge = MarmosetBridge()
        send_to = bridge.render_template(
            template="bake",
            mode=SEND_TO,
            model_path="/tmp/x.fbx",
            manifest_path="/tmp/x.materials.json",
            output_dir="/tmp/out",
        )
        roundtrip = bridge.render_template(
            template="bake",
            mode=ROUNDTRIP,
            model_path="/tmp/x.fbx",
            manifest_path="/tmp/x.materials.json",
            output_dir="/tmp/out",
        )
        self.assertIn("SHOULD_QUIT = False", send_to)
        self.assertIn("SHOULD_QUIT = True", roundtrip)
        # save path only populated when headless
        self.assertIn('SAVE_PATH = r""', send_to)
        self.assertIn("x.tbscene", roundtrip)

    def test_build_bake_pairs_manifest_classifies_meshes_under_suffix_group(self):
        """Maya-side helper walks each selected object's mesh descendants
        and classifies each via the parent chain. Toolbag will use the
        resulting JSON sidecar to override its (broken-by-import-flatten)
        chain walk."""
        # Simulate: bake_high group with two mesh children; bake_low group
        # with one mesh child; one mesh whose own name has _low.
        relatives = {
            # listRelatives(allDescendents=True, type='transform') -> list of descendants
            "|bake_high": ["|bake_high|mesh_a", "|bake_high|mesh_b"],
            "|bake_low": ["|bake_low|mesh_c"],
            "|loose_low": [],
            # listRelatives(shapes=True, type='mesh') for each transform
            "|bake_high|mesh_a:shapes": ["|bake_high|mesh_a|shape_a"],
            "|bake_high|mesh_b:shapes": ["|bake_high|mesh_b|shape_b"],
            "|bake_low|mesh_c:shapes": ["|bake_low|mesh_c|shape_c"],
            "|loose_low:shapes": ["|loose_low|loose_low_shape"],
            "|bake_high:shapes": [],  # group has no shape
            "|bake_low:shapes": [],
            # parent walks: leaf -> ... -> root
            "|bake_high|mesh_a:parent": ["|bake_high"],
            "|bake_high|mesh_b:parent": ["|bake_high"],
            "|bake_low|mesh_c:parent": ["|bake_low"],
            "|bake_high:parent": [],
            "|bake_low:parent": [],
            "|loose_low:parent": [],
        }

        def _list_relatives(node, **kw):
            if kw.get("allDescendents") and kw.get("type") == "transform":
                return list(relatives.get(node, []))
            if kw.get("shapes") and kw.get("type") == "mesh":
                return list(relatives.get(f"{node}:shapes", []))
            if kw.get("parent"):
                return list(relatives.get(f"{node}:parent", []))
            return []

        with unittest.mock.patch.object(
            mock_cmds, "listRelatives", side_effect=_list_relatives
        ):
            out = build_bake_pairs_manifest(
                ["|bake_high", "|bake_low", "|loose_low"], "_high", "_low"
            )

        # Bake_high contributes mesh_a + mesh_b -> 'high' (via parent chain).
        # Bake_low contributes mesh_c -> 'low' (via parent chain).
        # loose_low has _low in its OWN name -> 'low' (own-name match).
        self.assertEqual(
            out,
            {"mesh_a": "high", "mesh_b": "high", "mesh_c": "low", "loose_low": "low"},
        )

    def test_build_bake_pairs_manifest_returns_empty_when_no_suffixes(self):
        """If both suffixes are blank, no classification is possible.
        The helper must return an empty dict without scanning the scene."""
        # No listRelatives calls expected -- but we patch anyway to confirm
        # the helper bails before any DAG work.
        with unittest.mock.patch.object(
            mock_cmds, "listRelatives", return_value=[]
        ) as mock_lr:
            out = build_bake_pairs_manifest(["|anything"], "", "")
            self.assertEqual(out, {})
            mock_lr.assert_not_called()

    def test_snapshot_outputs_detects_psd_bake_files(self):
        """Regression: Toolbag's BakerObject writes per-map PSDs, so the
        roundtrip output-diff helper must include ``.psd`` in its
        extension list. Without it the bridge reports 'no new map files'
        after a successful bake."""
        with tempfile.TemporaryDirectory() as tmp:
            # Drop a representative set of files matching Toolbag's
            # ``<basename>_<MapSuffix>.psd`` output convention.
            for name in (
                "bake_Normal.psd",
                "bake_AO.psd",
                "bake_matid.psd",
                # Plus a non-map file that must NOT be picked up.
                "scene.tbscene",
            ):
                open(os.path.join(tmp, name), "wb").close()

            snap = MarmosetBridge._snapshot_outputs(tmp)
            self.assertEqual(
                {os.path.basename(p) for p in snap},
                {"bake_Normal.psd", "bake_AO.psd", "bake_matid.psd"},
            )

    def test_snapshot_outputs_since_filter_picks_up_overwrites(self):
        """Regression: Toolbag overwrites ``bake_*.psd`` in place on a
        re-bake. The old (path-only) snapshot returned an empty diff in
        that case so the bridge claimed "no new map files" after a
        successful bake into an output dir that already held PSDs.

        ``since=`` filters by mtime so overwritten files come back even
        when the path was already present. Untouched files are excluded.
        """
        import time

        with tempfile.TemporaryDirectory() as tmp:
            stale_path = os.path.join(tmp, "bake_Normal.psd")
            kept_path = os.path.join(tmp, "irrelevant_unchanged.psd")
            open(stale_path, "wb").close()
            open(kept_path, "wb").close()

            # Backdate both files to before the cutoff so they look like
            # leftovers from a previous session.
            old_mtime = time.time() - 60.0
            os.utime(stale_path, (old_mtime, old_mtime))
            os.utime(kept_path, (old_mtime, old_mtime))

            cutoff = time.time() - 5.0

            # Simulate Toolbag overwriting just the stale file: refresh
            # only its mtime, leave the other alone.
            now = time.time()
            os.utime(stale_path, (now, now))

            snap = MarmosetBridge._snapshot_outputs(tmp, since=cutoff)
            self.assertEqual(
                {os.path.basename(p) for p in snap},
                {"bake_Normal.psd"},
                "snapshot with since= filter must include overwrites and "
                "exclude untouched pre-existing files",
            )

            # Sanity: the unfiltered call still returns both.
            full = MarmosetBridge._snapshot_outputs(tmp)
            self.assertEqual(
                {os.path.basename(p) for p in full},
                {"bake_Normal.psd", "irrelevant_unchanged.psd"},
            )

    def test_parameters_referenced_keys(self):
        """referenced_keys returns only the registered placeholders a template uses."""
        bake = (_TEMPLATE_DIR / "bake.py").read_text(encoding="utf-8")
        used = _params.referenced_keys(bake)
        # bake.py exposes the bake-* and MAP_* + high/low knobs.
        for must_be_present in (
            "BAKE_SIZE",
            "BAKE_BITS",
            "MAP_NORMAL",
            "HIGH_SUFFIX",
        ):
            self.assertIn(must_be_present, used)
        # SKY_PRESET belongs to lookdev, not bake.
        self.assertNotIn("SKY_PRESET", used)


@unittest.skipUnless(
    _CMDS_IS_MOCKED, "Mock-based test -- run via pytest, not run_tests.py"
)
class TestResolveToolbagLogPath(unittest.TestCase):
    """The three-tier fallback must survive a Toolbag major-version bump."""

    def setUp(self):
        # Sandbox LOCALAPPDATA so we don't depend on the test machine's
        # real Marmoset install.
        self._fake_localappdata = tempfile.mkdtemp(prefix="marm_la_")
        self._env_patch = unittest.mock.patch.dict(
            os.environ, {"LOCALAPPDATA": self._fake_localappdata}, clear=False
        )
        self._env_patch.start()

    def tearDown(self):
        import shutil
        self._env_patch.stop()
        shutil.rmtree(self._fake_localappdata, ignore_errors=True)

    def _make_log(self, version_suffix, mtime_offset=0):
        """Create %LOCALAPPDATA%/Marmoset Toolbag <ver>/log.txt and return its path."""
        d = os.path.join(self._fake_localappdata, f"Marmoset Toolbag {version_suffix}")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "log.txt")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("stub")
        if mtime_offset:
            import time
            t = time.time() + mtime_offset
            os.utime(p, (t, t))
        return p

    def test_tier1_parses_version_from_program_files_install(self):
        """Real install layout: 'Marmoset\\Toolbag 5\\toolbag.exe' (backslash
        between Marmoset and Toolbag -- the LOCALAPPDATA dir uses a space
        instead, so the regex must work for both)."""
        self._make_log("5")
        expected = os.path.join(
            self._fake_localappdata, "Marmoset Toolbag 5", "log.txt"
        )
        exe = r"C:\Program Files\Marmoset\Toolbag 5\toolbag.exe"
        self.assertEqual(
            os.path.normpath(resolve_toolbag_log_path(exe)),
            os.path.normpath(expected),
        )

    def test_tier1_parses_version_from_space_separated_layout(self):
        """Some installers flatten to 'Marmoset Toolbag 5\\toolbag.exe'."""
        self._make_log("5")
        expected = os.path.join(
            self._fake_localappdata, "Marmoset Toolbag 5", "log.txt"
        )
        exe = r"C:\Custom\Marmoset Toolbag 5\toolbag.exe"
        self.assertEqual(
            os.path.normpath(resolve_toolbag_log_path(exe)),
            os.path.normpath(expected),
        )

    def test_tier1_works_for_hypothetical_future_version(self):
        """The same code path picks up Toolbag 6 without any source change."""
        expected = self._make_log("6")
        exe = r"D:\custom\Marmoset Toolbag 6\toolbag.exe"
        self.assertEqual(
            os.path.normpath(resolve_toolbag_log_path(exe)),
            os.path.normpath(expected),
        )

    def test_tier2_falls_back_to_newest_localappdata_log(self):
        """No version in exe path -> scan LOCALAPPDATA, newest log wins."""
        self._make_log("4", mtime_offset=-3600)   # 1h old
        newer = self._make_log("5", mtime_offset=0)
        exe = r"D:\nonstandard\bin\toolbag.exe"  # no 'Marmoset Toolbag N'
        self.assertEqual(
            os.path.normpath(resolve_toolbag_log_path(exe)),
            os.path.normpath(newer),
        )

    def test_returns_none_when_nothing_found(self):
        """No exe, no LOCALAPPDATA Toolbag dirs -> None."""
        self.assertIsNone(resolve_toolbag_log_path(None))
        self.assertIsNone(resolve_toolbag_log_path("/random/path/toolbag.exe"))

    def test_tier1_returns_path_even_when_log_does_not_exist_yet(self):
        """Fresh install: log.txt isn't written yet, but tier 1 must still
        return the correct version-derived path (Toolbag will create it
        as soon as it writes anything)."""
        # No log file created -- but a Toolbag 4 log exists to prove tier 2
        # is NOT being used.
        self._make_log("4")
        expected = os.path.join(
            self._fake_localappdata, "Marmoset Toolbag 5", "log.txt"
        )
        exe = r"C:\Program Files\Marmoset\Toolbag 5\toolbag.exe"
        self.assertEqual(
            os.path.normpath(resolve_toolbag_log_path(exe)),
            os.path.normpath(expected),
        )

    def test_regex_is_case_insensitive(self):
        """User-typed paths can have any case on Windows."""
        expected = self._make_log("5")
        for exe in (
            r"c:\program files\marmoset\toolbag 5\toolbag.exe",
            r"C:\PROGRAM FILES\MARMOSET\TOOLBAG 5\TOOLBAG.EXE",
        ):
            with self.subTest(exe=exe):
                self.assertEqual(
                    os.path.normpath(resolve_toolbag_log_path(exe)),
                    os.path.normpath(expected),
                )


@unittest.skipUnless(
    _CMDS_IS_MOCKED, "Mock-based test -- run via pytest, not run_tests.py"
)
class TestClassifyLogLine(unittest.TestCase):
    """Each Toolbag log line must route to the right severity so the
    bridge panel colour-codes errors visibly (red) and skips (yellow)."""

    def assertLevel(self, expected_level, line):
        result = classify_log_line(line)
        self.assertIsNotNone(result, f"Line was suppressed: {line!r}")
        actual_level, _ = result
        self.assertEqual(
            actual_level,
            expected_level,
            f"Expected {expected_level!r} for {line!r}, got {actual_level!r}",
        )

    def test_helper_error_marker_classified_as_error(self):
        self.assertLevel("error", "    ! roughness: MatField not found")
        self.assertLevel(
            "error", "    ! baseColor: file not found on disk -> /tex/x.png"
        )

    def test_toolbag_internal_errors_classified_as_error(self):
        self.assertLevel(
            "error",
            r"cannot open image C:\Users\alvin\extinguisher_Base_Color.png",
        )
        self.assertLevel("error", "MatField not found")
        self.assertLevel("error", "Traceback (most recent call last):")
        self.assertLevel(
            "error", "AttributeError: module 'mset' has no attribute 'X'"
        )

    def test_helper_skip_classified_as_warning(self):
        self.assertLevel(
            "warning", "  SKIP  'OrphanMat' -- no matching Toolbag material."
        )

    def test_helper_empty_manifest_classified_as_warning(self):
        """If the manifest produced no materials the wire pass did
        nothing -- the user MUST see this in yellow, not silent info."""
        self.assertLevel(
            "warning",
            "[toolbag_helpers] Manifest empty or missing at: /tmp/x.json",
        )
        self.assertLevel(
            "warning",
            "[toolbag_helpers] Nothing to wire -- check Maya-side MatManifest.build().",
        )

    def test_helper_no_sky_classified_as_warning(self):
        self.assertLevel(
            "warning",
            "[toolbag_helpers] No SkyBoxObject in scene; skipping sky preset.",
        )

    def test_helper_question_classified_as_warning(self):
        self.assertLevel(
            "warning", "    ? No Toolbag mapping for slot 'foo', skipping."
        )

    def test_helper_success_classified_as_info(self):
        self.assertLevel(
            "info", "    + baseColor -> 'Albedo Map' = body_BC.png"
        )

    def test_helper_status_classified_as_info(self):
        self.assertLevel("info", "[toolbag_helpers] Scene contains 2 material(s).")
        self.assertLevel(
            "info", "[Maya->Toolbag] FBX: C:/tmp/x.fbx"
        )

    def test_preload_chatter_is_suppressed(self):
        """Toolbag's shader/image preload spam must not be forwarded."""
        for noise in (
            "opening code data/shader/common/util.sh",
            "opening image data/gui/control/windowbg.tga",
            "opening shader data/shader/post/post.frag",
        ):
            with self.subTest(line=noise):
                self.assertIsNone(classify_log_line(noise))

    def test_empty_line_is_suppressed(self):
        self.assertIsNone(classify_log_line(""))
        self.assertIsNone(classify_log_line("   "))


@unittest.skipUnless(
    _CMDS_IS_MOCKED, "Mock-based test -- run via pytest, not run_tests.py"
)
class TestDispatchLogLines(unittest.TestCase):
    """End-to-end: a sequence of lines drives a real Python logger
    through info/warning/error per the classifier rules."""

    def test_each_level_lands_on_the_matching_logger_call(self):
        logger = unittest.mock.MagicMock()
        lines = [
            "[toolbag_helpers] Scene contains 1 material(s).",   # info
            "  SKIP  'X' -- no matching Toolbag material.",      # warning
            "    ! roughness: MatField not found",               # error
            "opening image data/gui/control/foo.tga",            # suppressed
            "    + baseColor -> 'Albedo Map' = file.png",        # info
        ]
        dispatch_log_lines(lines, logger)

        self.assertEqual(logger.info.call_count, 2)
        self.assertEqual(logger.warning.call_count, 1)
        self.assertEqual(logger.error.call_count, 1)


@unittest.skipUnless(
    _CMDS_IS_MOCKED, "Mock-based test -- run via pytest, not run_tests.py"
)
class TestToolbagLogTail(unittest.TestCase):
    """The tail thread must read new content as it's written and stop
    when the simulated process exits. This is the real send_to flow,
    minus actually running Toolbag."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="tb_tail_")
        self.log_path = os.path.join(self._tmpdir, "log.txt")
        # Pre-seed the file with prior-session content; the tail must
        # NOT replay it.
        with open(self.log_path, "w", encoding="utf-8") as fh:
            fh.write("prior session line\n")
        self.start_offset = os.path.getsize(self.log_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _append(self, *lines):
        """Append *lines* (each newline-terminated) to the log file."""
        with open(self.log_path, "a", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line + "\n")
                fh.flush()

    def test_streams_appended_lines_until_process_exits(self):
        import time

        class FakeProcess:
            def __init__(self):
                self._alive = True
            def poll(self):
                return None if self._alive else 0

        proc = FakeProcess()
        logger = unittest.mock.MagicMock()

        thread = start_toolbag_log_tail(
            self.log_path, self.start_offset, proc, logger, poll_interval=0.05
        )

        # Simulate Toolbag writing during the run.
        self._append(
            "[Maya->Toolbag] FBX: scene.fbx",
            "    ! roughness: MatField not found",
            "    + baseColor -> 'Albedo Map' = body_BC.png",
        )

        # Give the thread time to pick up the new content.
        for _ in range(40):
            if logger.info.call_count >= 2 and logger.error.call_count >= 1:
                break
            time.sleep(0.05)

        proc._alive = False  # Simulate Toolbag exit.
        thread.join(timeout=2.0)
        self.assertFalse(thread.is_alive(), "Tail thread didn't exit.")

        # The prior-session line must NOT be dispatched at ANY level --
        # checking only logger.info would let it leak through a buggy
        # severity routing.
        for level in ("info", "warning", "error", "debug"):
            for call in getattr(logger, level).call_args_list:
                self.assertNotIn(
                    "prior session line", str(call.args[0] if call.args else "")
                )

        # Each appended line landed on the matching log level.
        self.assertEqual(logger.error.call_count, 1)
        self.assertGreaterEqual(logger.info.call_count, 2)

    def test_does_not_crash_when_log_file_missing(self):
        """A missing log file must not propagate IO errors out of the thread."""
        os.remove(self.log_path)

        class FakeProcess:
            def poll(self):
                return 0   # already exited

        thread = start_toolbag_log_tail(
            self.log_path, 0, FakeProcess(), unittest.mock.MagicMock(),
            poll_interval=0.05,
        )
        thread.join(timeout=2.0)
        # If we got here without raising, the defensive try/except worked.
        self.assertFalse(thread.is_alive())

    def test_waits_for_log_file_to_be_created(self):
        """Fresh-install scenario: log.txt doesn't exist when the tail
        thread starts, then Toolbag creates it and writes content. The
        tail must pick the content up rather than giving up at open()."""
        import time

        os.remove(self.log_path)  # File doesn't exist yet.

        class FakeProcess:
            def __init__(self):
                self._alive = True
            def poll(self):
                return None if self._alive else 0

        proc = FakeProcess()
        logger = unittest.mock.MagicMock()

        thread = start_toolbag_log_tail(
            self.log_path, 0, proc, logger,
            poll_interval=0.05,
            file_wait_timeout=5.0,
        )

        # Simulate Toolbag taking a moment to create the log file.
        time.sleep(0.2)
        with open(self.log_path, "w", encoding="utf-8") as fh:
            fh.write("[toolbag_helpers] Scene contains 1 material(s).\n")
            fh.write("    + baseColor -> 'Albedo Map' = body.png\n")
            fh.flush()

        # Wait for the thread to pick up the content.
        for _ in range(40):
            if logger.info.call_count >= 2:
                break
            time.sleep(0.05)

        proc._alive = False
        thread.join(timeout=2.0)
        self.assertFalse(thread.is_alive())
        self.assertGreaterEqual(logger.info.call_count, 2)

    def test_file_wait_gives_up_when_process_exits(self):
        """If Toolbag dies before creating log.txt, the thread must exit
        cleanly rather than spinning until the wait timeout."""
        import time

        os.remove(self.log_path)

        class FakeProcess:
            def __init__(self):
                self._alive = True
            def poll(self):
                return None if self._alive else 0

        proc = FakeProcess()
        thread = start_toolbag_log_tail(
            self.log_path, 0, proc, unittest.mock.MagicMock(),
            poll_interval=0.05,
            file_wait_timeout=30.0,  # Generous -- we should exit on process death, not timeout.
        )

        time.sleep(0.1)
        proc._alive = False  # Kill the process before the file appears.

        thread.join(timeout=2.0)
        self.assertFalse(
            thread.is_alive(),
            "Thread must exit on process death even if log never appeared",
        )


if __name__ == "__main__":
    unittest.main()
