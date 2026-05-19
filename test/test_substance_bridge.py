# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.mat_utils.substance_bridge._substance_bridge.

No Maya runtime required -- covers template discovery, metadata parsing,
type validation, and mode filtering. The full bridge.send() flow needs
Maya for FBX export and is covered separately by the Maya test suite.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mayatk.mat_utils.substance_bridge._substance_bridge import (
    SEND_TO,
    ROUNDTRIP,
    TARGET_AUTO,
    TARGET_NEW,
    TARGET_CURRENT,
    SubstanceBridge,
    list_templates,
    list_template_modes,
    parse_template,
    resolve_painter_log_path,
    _TEMPLATE_DEFAULTS,
)


class TestTemplateDiscovery(unittest.TestCase):
    def test_list_templates_finds_import(self):
        stems = [p.stem for p in list_templates()]
        self.assertIn("import", stems)

    def test_list_templates_skips_underscore_prefixed(self):
        # Sanity: __init__.py is in templates/ but starts with underscore
        # and must not be reported as a user template.
        stems = [p.stem for p in list_templates()]
        self.assertNotIn("__init__", stems)

    def test_list_template_modes_returns_pairs(self):
        pairs = list_template_modes()
        self.assertIn(("import", SEND_TO), pairs)


class TestParseTemplate(unittest.TestCase):
    """parse_template should defend against every shape of broken template."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="substance_template_test_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, name: str, body: str) -> Path:
        path = Path(self.tmpdir) / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_import_template_parses_correctly(self):
        path = next(p for p in list_templates() if p.stem == "import")
        meta = parse_template(path)
        self.assertEqual(meta["BRIDGE_MODES"], (SEND_TO,))
        # LAUNCH_ARGS now references the user-tunable PARAMS that mirror
        # Painter's New Project dialog -- the bridge surfaces these as
        # widgets in the slot panel.
        self.assertEqual(meta["LAUNCH_ARGS"][:2], ["--mesh", "__FBX_PATH__"])
        self.assertIn("__PAINTER_RESOLUTION__", meta["LAUNCH_ARGS"])
        self.assertEqual(meta["RPC_SCRIPT"], "")
        # import.py builds a manifest (folded in from the deleted with_textures
        # template) and embeds Maya-referenced textures into the FBX.
        self.assertEqual(meta["BUILD_MANIFEST"], True)
        self.assertTrue(meta["FBX_OPTIONS"].get("FBXExportEmbeddedTextures"))

    def test_missing_constants_fall_back_to_defaults(self):
        path = self._write("blank.py", '"""empty template"""\n')
        meta = parse_template(path)
        # Defaults: SEND_TO mode, empty args, empty script, no manifest.
        self.assertEqual(meta["BRIDGE_MODES"], (SEND_TO,))
        self.assertEqual(meta["LAUNCH_ARGS"], _TEMPLATE_DEFAULTS["LAUNCH_ARGS"])
        self.assertEqual(meta["RPC_SCRIPT"], "")
        self.assertEqual(meta["BUILD_MANIFEST"], False)

    def test_invalid_mode_is_filtered_out(self):
        path = self._write(
            "bogus.py",
            'BRIDGE_MODES = ("send_to", "garbage_mode")\n',
        )
        meta = parse_template(path)
        self.assertEqual(meta["BRIDGE_MODES"], (SEND_TO,))

    def test_all_invalid_modes_falls_back_to_send_to(self):
        path = self._write("worse.py", 'BRIDGE_MODES = ("invalid",)\n')
        meta = parse_template(path)
        self.assertEqual(meta["BRIDGE_MODES"], (SEND_TO,))

    def test_roundtrip_mode_preserved(self):
        path = self._write(
            "rt.py",
            'BRIDGE_MODES = ("send_to", "roundtrip")\n'
            'RPC_SCRIPT = "alg.log(\'hi\')"\n',
        )
        meta = parse_template(path)
        self.assertEqual(meta["BRIDGE_MODES"], (SEND_TO, ROUNDTRIP))
        self.assertIn("alg.log", meta["RPC_SCRIPT"])

    def test_non_literal_value_falls_back(self):
        # An expression (not a literal) should be rejected gracefully.
        path = self._write(
            "expr.py",
            'BAKE_W = 2048\nLAUNCH_ARGS = ["--w", str(BAKE_W)]\n',
        )
        meta = parse_template(path)
        # Non-literal LAUNCH_ARGS -> fall back to default empty list.
        self.assertEqual(meta["LAUNCH_ARGS"], _TEMPLATE_DEFAULTS["LAUNCH_ARGS"])

    def test_wrong_type_falls_back(self):
        path = self._write(
            "wrong_type.py",
            'LAUNCH_ARGS = "not a list"\nRPC_SCRIPT = 42\n',
        )
        meta = parse_template(path)
        self.assertEqual(meta["LAUNCH_ARGS"], _TEMPLATE_DEFAULTS["LAUNCH_ARGS"])
        self.assertEqual(meta["RPC_SCRIPT"], _TEMPLATE_DEFAULTS["RPC_SCRIPT"])

    def test_non_string_launch_arg_entry_falls_back(self):
        # All entries in LAUNCH_ARGS must be strings.
        path = self._write(
            "mixed.py",
            'LAUNCH_ARGS = ["--scale", 1.5]\n',
        )
        meta = parse_template(path)
        self.assertEqual(meta["LAUNCH_ARGS"], _TEMPLATE_DEFAULTS["LAUNCH_ARGS"])

    def test_syntax_error_falls_back(self):
        path = self._write("syntax.py", "this is not python {{[\n")
        meta = parse_template(path)
        # All defaults preserved -- compare to the canonical defaults dict
        # (with BRIDGE_MODES normalized to a tuple) so adding a new field
        # to _TEMPLATE_DEFAULTS doesn't break this test.
        expected = dict(_TEMPLATE_DEFAULTS)
        expected["BRIDGE_MODES"] = (SEND_TO,)
        self.assertEqual(meta, expected)

    def test_missing_file_falls_back(self):
        meta = parse_template(Path(self.tmpdir) / "does_not_exist.py")
        self.assertEqual(meta["BRIDGE_MODES"], (SEND_TO,))

    def test_list_normalized_to_tuple(self):
        # Author might use a list instead of a tuple.
        path = self._write("list_modes.py", 'BRIDGE_MODES = ["send_to"]\n')
        meta = parse_template(path)
        self.assertEqual(meta["BRIDGE_MODES"], (SEND_TO,))


class TestFbxOptionsField(unittest.TestCase):
    """FBX_OPTIONS template field parsing + merge precedence."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="substance_fbxopts_test_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, name, body):
        path = Path(self.tmpdir) / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_missing_field_defaults_to_empty_dict(self):
        path = self._write("blank.py", '"""empty"""\n')
        meta = parse_template(path)
        self.assertEqual(meta["FBX_OPTIONS"], {})

    def test_dict_value_parsed(self):
        path = self._write(
            "with_opts.py",
            'FBX_OPTIONS = {"FBXExportEmbeddedTextures": True, '
            '"FBXExportTriangulate": True}\n',
        )
        meta = parse_template(path)
        self.assertEqual(
            meta["FBX_OPTIONS"],
            {"FBXExportEmbeddedTextures": True, "FBXExportTriangulate": True},
        )

    def test_wrong_type_falls_back(self):
        path = self._write("bad.py", 'FBX_OPTIONS = "not a dict"\n')
        meta = parse_template(path)
        self.assertEqual(meta["FBX_OPTIONS"], {})

    def test_non_literal_falls_back(self):
        path = self._write("expr.py", "FBX_OPTIONS = dict(a=1)\n")
        meta = parse_template(path)
        self.assertEqual(meta["FBX_OPTIONS"], {})


class TestExportFbxField(unittest.TestCase):
    """EXPORT_FBX template field controls whether send() exports an FBX."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="substance_export_test_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, name, body):
        path = Path(self.tmpdir) / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_default_is_true(self):
        path = self._write("blank.py", '"""empty"""\n')
        meta = parse_template(path)
        self.assertEqual(meta["EXPORT_FBX"], True)

    def test_explicit_false_parses(self):
        path = self._write("r.py", "EXPORT_FBX = False\n")
        meta = parse_template(path)
        self.assertEqual(meta["EXPORT_FBX"], False)

    def test_wrong_type_falls_back_to_true(self):
        path = self._write("r.py", 'EXPORT_FBX = "no"\n')
        meta = parse_template(path)
        self.assertEqual(meta["EXPORT_FBX"], True)


class TestBundledTemplates(unittest.TestCase):
    """Sanity checks for the canonical templates the bridge ships with."""

    def test_bundled_templates_full_set(self):
        """Bundled set: import (new project), reimport (update current),
        render (Iray render current), bake_lighting (import + bake Iray
        lighting into diffuse). Guards against accidental drift."""
        stems = sorted(p.stem for p in list_templates())
        self.assertEqual(
            stems, ["bake_lighting", "import", "reimport", "render"]
        )

    def test_import_template_embeds_textures_and_builds_manifest(self):
        path = next(p for p in list_templates() if p.stem == "import")
        meta = parse_template(path)
        self.assertEqual(meta["BRIDGE_MODES"], (SEND_TO,))
        self.assertTrue(meta["FBX_OPTIONS"].get("FBXExportEmbeddedTextures"),
                        "import.py must embed textures (replaces with_textures)")
        self.assertTrue(meta["BUILD_MANIFEST"])
        self.assertEqual(meta["TARGET_INSTANCE"], "new")

    def test_reimport_is_send_to_not_roundtrip(self):
        """Reimport is a one-way update of an existing instance, not a
        roundtrip -- nothing comes back from Painter."""
        path = next(p for p in list_templates() if p.stem == "reimport")
        meta = parse_template(path)
        self.assertEqual(meta["BRIDGE_MODES"], (SEND_TO,))
        self.assertEqual(meta["TARGET_INSTANCE"], "current")
        self.assertTrue(meta["RPC_SCRIPT"].strip())

    def test_render_template_skips_fbx_and_targets_current(self):
        """render.py asks the running Painter to Iray-render itself; no
        Maya FBX export needed, and it requires a live managed instance."""
        path = next(p for p in list_templates() if p.stem == "render")
        meta = parse_template(path)
        self.assertEqual(meta["BRIDGE_MODES"], (SEND_TO,))
        self.assertEqual(meta["TARGET_INSTANCE"], "current")
        self.assertFalse(meta["EXPORT_FBX"])
        self.assertEqual(meta["LAUNCH_ARGS"], [])
        self.assertIn("exportRenderImage", meta["RPC_SCRIPT"])

    def test_bake_lighting_combines_import_and_iray_render(self):
        """bake_lighting.py = import.py (new project + embed textures) +
        a Painter-side Iray render that lands in the diffuse channel."""
        path = next(p for p in list_templates() if p.stem == "bake_lighting")
        meta = parse_template(path)
        self.assertEqual(meta["BRIDGE_MODES"], (SEND_TO,))
        self.assertEqual(meta["TARGET_INSTANCE"], "new")
        # FBX is exported (it's the source of the new project).
        self.assertTrue(meta["EXPORT_FBX"])
        self.assertTrue(meta["FBX_OPTIONS"].get("FBXExportEmbeddedTextures"))
        self.assertTrue(meta["BUILD_MANIFEST"])
        # RPC body covers all three Painter-side phases.
        self.assertIn('alg.shaders.setCurrent("iray")', meta["RPC_SCRIPT"])
        self.assertIn("exportRenderImage", meta["RPC_SCRIPT"])
        self.assertIn("insertLayerInstance", meta["RPC_SCRIPT"])
        self.assertIn("baseColor", meta["RPC_SCRIPT"])


class TestParameterRendering(unittest.TestCase):
    """parameters.render_cli_context vs render_js_context: the bug we fixed."""

    def test_format_cli_string_is_raw(self):
        """CLI rendering must NOT auto-quote strings -- subprocess would
        otherwise embed literal quotes inside argv values."""
        from mayatk.mat_utils.substance_bridge.parameters import (
            SubstanceParam, render_cli_context,
        )
        spec = SubstanceParam(
            key="P", label="P", widget_type="path", default="",
        )
        # Inject a one-off spec into a synthetic context dict; the function
        # consults PARAMS at lookup time, so use an unregistered key via the
        # str() fallback to exercise the raw path independently.
        out = render_cli_context({"UNKNOWN_KEY": "C:/some/path"})
        self.assertEqual(out["UNKNOWN_KEY"], "C:/some/path")
        # Direct spec test:
        self.assertEqual(spec.format_cli("C:/Painter/template.spp"),
                         "C:/Painter/template.spp")

    def test_format_js_string_is_quoted_and_escaped(self):
        from mayatk.mat_utils.substance_bridge.parameters import SubstanceParam
        spec = SubstanceParam(
            key="P", label="P", widget_type="path", default="",
        )
        # Backslashes doubled, quotes escaped, wrapped in double quotes.
        self.assertEqual(spec.format_js("C:\\foo\\bar"), '"C:\\\\foo\\\\bar"')
        self.assertEqual(spec.format_js('say "hi"'), '"say \\"hi\\""')

    def test_format_cli_bool_lowercased(self):
        from mayatk.mat_utils.substance_bridge.parameters import SubstanceParam
        spec = SubstanceParam(
            key="B", label="B", widget_type="bool", default=False,
        )
        self.assertEqual(spec.format_cli(True), "true")
        self.assertEqual(spec.format_cli(False), "false")

    def test_format_cli_int_plain(self):
        from mayatk.mat_utils.substance_bridge.parameters import SubstanceParam
        spec = SubstanceParam(
            key="N", label="N", widget_type="int", default=0,
        )
        self.assertEqual(spec.format_cli(2048), "2048")

    def test_format_cli_file_list_joins_with_pathsep(self):
        import os as _os
        from mayatk.mat_utils.substance_bridge.parameters import SubstanceParam
        spec = SubstanceParam(
            key="L", label="L", widget_type="file_list", default=[],
        )
        joined = spec.format_cli(["a.png", "b.png"])
        self.assertEqual(joined, _os.pathsep.join(["a.png", "b.png"]))


class TestBakedMapStaging(unittest.TestCase):
    """_stage_file_list_params copies files alongside the FBX export."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="substance_baked_test_")
        # Create two fake baked-map files in a 'src' subdir.
        src_dir = Path(self.tmpdir) / "src"
        src_dir.mkdir()
        self.src_ao = src_dir / "obj_ao.png"
        self.src_normal = src_dir / "obj_normal.png"
        self.src_ao.write_bytes(b"fake-ao")
        self.src_normal.write_bytes(b"fake-normal")
        self.out_dir = Path(self.tmpdir) / "out"
        self.out_dir.mkdir()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_copies_files_into_output_dir(self):
        bridge = SubstanceBridge()
        staged = bridge._stage_file_list_params(
            {"PAINTER_BAKED_MAPS": [str(self.src_ao), str(self.src_normal)]},
            str(self.out_dir),
        )
        self.assertIn("PAINTER_BAKED_MAPS", staged)
        self.assertEqual(len(staged["PAINTER_BAKED_MAPS"]), 2)
        for dst in staged["PAINTER_BAKED_MAPS"]:
            self.assertTrue(Path(dst).is_file(),
                            f"staged file missing: {dst}")
            self.assertEqual(Path(dst).parent, self.out_dir)

    def test_missing_source_is_skipped(self):
        bridge = SubstanceBridge()
        staged = bridge._stage_file_list_params(
            {"PAINTER_BAKED_MAPS": [
                str(self.src_ao),
                str(Path(self.tmpdir) / "does_not_exist.png"),
            ]},
            str(self.out_dir),
        )
        self.assertEqual(len(staged["PAINTER_BAKED_MAPS"]), 1)

    def test_empty_list_produces_no_staging_entry(self):
        bridge = SubstanceBridge()
        staged = bridge._stage_file_list_params(
            {"PAINTER_BAKED_MAPS": []}, str(self.out_dir)
        )
        self.assertEqual(staged, {})

    def test_no_param_value_is_a_no_op(self):
        bridge = SubstanceBridge()
        staged = bridge._stage_file_list_params({}, str(self.out_dir))
        self.assertEqual(staged, {})

    def test_referenced_keys_gates_staging(self):
        """When *referenced_keys* is supplied, only PARAMS in that set get
        staged -- a stale baked-maps list in the panel doesn't pollute a
        render-template send."""
        bridge = SubstanceBridge()
        # Empty set => nothing staged even though PAINTER_BAKED_MAPS has files.
        staged = bridge._stage_file_list_params(
            {"PAINTER_BAKED_MAPS": [str(self.src_ao)]},
            str(self.out_dir),
            referenced_keys=set(),
        )
        self.assertEqual(staged, {})

        # Including the key in referenced_keys re-enables staging.
        staged = bridge._stage_file_list_params(
            {"PAINTER_BAKED_MAPS": [str(self.src_ao)]},
            str(self.out_dir),
            referenced_keys={"PAINTER_BAKED_MAPS"},
        )
        self.assertEqual(len(staged["PAINTER_BAKED_MAPS"]), 1)


class TestRenderTemplateJs(unittest.TestCase):
    """Render template's RPC_SCRIPT must produce valid JS after substitution.

    Specifically, the OUTPUT_DIR internal token must land inside JS quotes
    (template-author-supplied) so the fallback expression
    ``("__OUTPUT_DIR__" + "/painter_render.png")`` doesn't degenerate into
    a bare-identifier parse error like ``C:/path + "/painter_render.png"``.
    """

    def _render(self, params=None):
        bridge = SubstanceBridge()
        path = next(p for p in list_templates() if p.stem == "render")
        meta = parse_template(path)
        _cli, js_ctx = bridge._build_contexts(
            fbx_path="/tmp/x.fbx",
            manifest_path="/tmp/x.materials.json",
            output_dir="/tmp/render_test",
            params=params,
        )
        from pythontk.str_utils._str_utils import StrUtils as _StrUtils
        return _StrUtils.replace_delimited(meta["RPC_SCRIPT"], js_ctx)

    def test_internal_output_dir_token_is_quoted(self):
        rendered = self._render(params=None)
        # The OUTPUT_DIR fallback should appear inside JS double quotes,
        # not as a bare identifier.
        self.assertIn('"/tmp/render_test"', rendered)
        # And the user-side path PARAM defaults to empty -> JS empty string.
        self.assertIn('"" ||', rendered)

    def test_user_supplied_path_overrides_fallback(self):
        rendered = self._render(
            params={"PAINTER_RENDER_OUTPUT_PATH": "C:/out/hero.png"},
        )
        self.assertIn('"C:/out/hero.png"', rendered)

    def test_numeric_params_render_unquoted(self):
        """Width/height/samples are ints -- they should drop into the JS
        body bare (no JS quotes) so they're treated as numeric literals."""
        rendered = self._render(
            params={
                "PAINTER_RENDER_WIDTH": 1920,
                "PAINTER_RENDER_HEIGHT": 1080,
                "PAINTER_RENDER_SAMPLES": 512,
            },
        )
        self.assertIn("width: 1920", rendered)
        self.assertIn("height: 1080", rendered)
        self.assertIn("samples: 512", rendered)


class TestBakeLightingTemplateJs(unittest.TestCase):
    """bake_lighting.py shares the same JS-quoting pitfalls as render.py.
    Lock in that __OUTPUT_DIR__ lands inside JS string quotes."""

    def _render(self, params=None):
        bridge = SubstanceBridge()
        path = next(p for p in list_templates() if p.stem == "bake_lighting")
        meta = parse_template(path)
        _cli, js_ctx = bridge._build_contexts(
            fbx_path="/tmp/x.fbx",
            manifest_path="/tmp/x.materials.json",
            output_dir="/tmp/bake_test",
            params=params,
        )
        from pythontk.str_utils._str_utils import StrUtils as _StrUtils
        return _StrUtils.replace_delimited(meta["RPC_SCRIPT"], js_ctx)

    def test_output_dir_token_is_quoted(self):
        rendered = self._render(params=None)
        self.assertIn('"/tmp/bake_test"', rendered)
        # Default render output path is empty -> JS empty string + ||.
        self.assertIn('"" ||', rendered)

    def test_user_supplied_render_path_overrides_fallback(self):
        rendered = self._render(
            params={"PAINTER_RENDER_OUTPUT_PATH": "C:/bakes/baked.png"},
        )
        self.assertIn('"C:/bakes/baked.png"', rendered)


class TestParamsPopulated(unittest.TestCase):
    """The infrastructure was previously scaffolded but PARAMS was empty.
    Guard against regressing back to an empty registry."""

    def test_params_dict_not_empty(self):
        from mayatk.mat_utils.substance_bridge.parameters import PARAMS
        self.assertGreater(len(PARAMS), 0,
                           "parameters.PARAMS must expose at least one knob "
                           "or the slot UI shows an empty panel")

    def test_import_template_references_params(self):
        """import.py must reference at least one registered PARAM key
        so the rendered LAUNCH_ARGS exercise the new rendering pipeline."""
        from mayatk.mat_utils.substance_bridge import parameters as _params
        path = next(
            (p for p in list_templates() if p.stem == "import"), None,
        )
        self.assertIsNotNone(path)
        used = _params.referenced_keys(path.read_text(encoding="utf-8"))
        self.assertGreater(
            len(used), 0,
            "with_textures.py should reference at least one PARAMS key "
            "(e.g. __PAINTER_RESOLUTION__) to surface a user knob",
        )


class TestEndToEndLaunchArgsRendering(unittest.TestCase):
    """End-to-end: load import.py, render LAUNCH_ARGS, verify CLI-clean."""

    def test_import_renders_to_clean_argv(self):
        bridge = SubstanceBridge()
        path = next(p for p in list_templates() if p.stem == "import")
        meta = parse_template(path)

        cli_ctx, _js_ctx = bridge._build_contexts(
            fbx_path="/tmp/x.fbx",
            manifest_path="/tmp/x.materials.json",
            output_dir="/tmp",
            params=None,  # defaults
        )

        rendered = bridge._render_launch_args(meta["LAUNCH_ARGS"], cli_ctx)

        # No argv entry should contain quote characters -- subprocess would
        # otherwise embed them inside the actual argument value.
        for arg in rendered:
            self.assertNotIn('"', arg,
                             f"argv entry has literal quotes: {arg!r}")
            self.assertNotIn("'", arg,
                             f"argv entry has literal quotes: {arg!r}")

        # FBX_PATH substituted; param keys substituted with their defaults.
        self.assertIn("/tmp/x.fbx", rendered)
        self.assertIn("2048", rendered)         # PAINTER_RESOLUTION default
        self.assertIn("OpenGL", rendered)        # PAINTER_NORMAL_FORMAT default
        self.assertIn("UV", rendered)            # PAINTER_UV_TILE_MODE default

    def test_user_params_override_defaults(self):
        bridge = SubstanceBridge()
        path = next(p for p in list_templates() if p.stem == "import")
        meta = parse_template(path)

        cli_ctx, _ = bridge._build_contexts(
            fbx_path="/tmp/x.fbx",
            manifest_path="/tmp/x.materials.json",
            output_dir="/tmp",
            params={"PAINTER_RESOLUTION": 4096, "PAINTER_NORMAL_FORMAT": "DirectX"},
        )
        rendered = bridge._render_launch_args(meta["LAUNCH_ARGS"], cli_ctx)
        self.assertIn("4096", rendered)
        self.assertIn("DirectX", rendered)

    def test_empty_project_template_pair_is_dropped(self):
        """``--template`` followed by an empty rendered value gets stripped
        so we don't ship Painter a broken ``--template ""`` argv pair."""
        bridge = SubstanceBridge()
        path = next(p for p in list_templates() if p.stem == "import")
        meta = parse_template(path)

        cli_ctx, _ = bridge._build_contexts(
            fbx_path="/tmp/x.fbx",
            manifest_path="/tmp/x.materials.json",
            output_dir="/tmp",
            params=None,  # PAINTER_PROJECT_TEMPLATE defaults to ""
        )
        rendered = bridge._render_launch_args(meta["LAUNCH_ARGS"], cli_ctx)
        self.assertNotIn("--template", rendered)
        self.assertNotIn("", rendered)

    def test_populated_project_template_passes_through(self):
        bridge = SubstanceBridge()
        path = next(p for p in list_templates() if p.stem == "import")
        meta = parse_template(path)

        cli_ctx, _ = bridge._build_contexts(
            fbx_path="/tmp/x.fbx",
            manifest_path="/tmp/x.materials.json",
            output_dir="/tmp",
            params={"PAINTER_PROJECT_TEMPLATE": "C:/templates/foo.spt"},
        )
        rendered = bridge._render_launch_args(meta["LAUNCH_ARGS"], cli_ctx)
        self.assertIn("--template", rendered)
        self.assertIn("C:/templates/foo.spt", rendered)

    def test_render_launch_args_drops_only_flag_empty_pairs(self):
        """Sanity: non-flag entries followed by empty strings are preserved
        (the heuristic only triggers when the empty value comes after a
        flag-style ``--`` or ``-`` token)."""
        bridge = SubstanceBridge()
        # ``--flag2`` is dropped because its value renders empty; the bare
        # positional ``""`` value following ``positional`` is preserved.
        result = bridge._render_launch_args(
            ["positional", "__EMPTY__", "--flag2", "__EMPTY__", "--keep", "tail"],
            {"EMPTY": ""},
        )
        self.assertEqual(result, ["positional", "", "--keep", "tail"])


class TestPanelSurfacesAllPainterDialogOptions(unittest.TestCase):
    """Each registered PARAM must be referenced by at least one bundled
    template -- otherwise the widget is defined but never visible in the
    panel for any template selection."""

    def test_every_param_referenced_by_some_template(self):
        from mayatk.mat_utils.substance_bridge import parameters as _params

        referenced = set()
        for path in list_templates():
            referenced |= _params.referenced_keys(
                path.read_text(encoding="utf-8")
            )
        missing = set(_params.PARAMS.keys()) - referenced
        self.assertFalse(
            missing,
            "Every PARAM must be referenced by at least one bundled "
            f"template; the panel will never surface: {sorted(missing)}",
        )


class TestPainterLogResolution(unittest.TestCase):
    def test_returns_string_or_none(self):
        # No assertion on existence -- LOCALAPPDATA may or may not have the
        # file. We only verify the function returns the right shape.
        result = resolve_painter_log_path()
        self.assertTrue(result is None or isinstance(result, str))


class TestTargetInstanceParsing(unittest.TestCase):
    """TARGET_INSTANCE field parsing + normalization."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="substance_target_test_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, name, body):
        path = Path(self.tmpdir) / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_default_is_auto(self):
        path = self._write("blank.py", '"""empty"""\n')
        meta = parse_template(path)
        self.assertEqual(meta["TARGET_INSTANCE"], TARGET_AUTO)

    def test_explicit_new(self):
        path = self._write("new.py", 'TARGET_INSTANCE = "new"\n')
        meta = parse_template(path)
        self.assertEqual(meta["TARGET_INSTANCE"], TARGET_NEW)

    def test_explicit_current(self):
        path = self._write("cur.py", 'TARGET_INSTANCE = "current"\n')
        meta = parse_template(path)
        self.assertEqual(meta["TARGET_INSTANCE"], TARGET_CURRENT)

    def test_invalid_value_falls_back_to_default(self):
        path = self._write("bad.py", 'TARGET_INSTANCE = "bogus"\n')
        meta = parse_template(path)
        self.assertEqual(meta["TARGET_INSTANCE"], TARGET_AUTO)

    def test_wrong_type_falls_back(self):
        path = self._write("bad.py", "TARGET_INSTANCE = 42\n")
        meta = parse_template(path)
        self.assertEqual(meta["TARGET_INSTANCE"], TARGET_AUTO)


class TestTargetValidation(unittest.TestCase):
    """SubstanceBridge._validate_target rejects incompatible pairs."""

    def test_auto_template_accepts_anything(self):
        # All four shapes must be valid for an auto template.
        for user_target in (TARGET_AUTO, TARGET_NEW, TARGET_CURRENT, 8090):
            SubstanceBridge._validate_target(TARGET_AUTO, user_target)

    def test_new_template_rejects_current(self):
        with self.assertRaises(ValueError):
            SubstanceBridge._validate_target(TARGET_NEW, TARGET_CURRENT)

    def test_new_template_rejects_int_port(self):
        with self.assertRaises(ValueError):
            SubstanceBridge._validate_target(TARGET_NEW, 8090)

    def test_new_template_accepts_auto_and_new(self):
        SubstanceBridge._validate_target(TARGET_NEW, TARGET_AUTO)
        SubstanceBridge._validate_target(TARGET_NEW, TARGET_NEW)

    def test_current_template_rejects_new(self):
        with self.assertRaises(ValueError):
            SubstanceBridge._validate_target(TARGET_CURRENT, TARGET_NEW)

    def test_current_template_accepts_current_and_int(self):
        SubstanceBridge._validate_target(TARGET_CURRENT, TARGET_CURRENT)
        SubstanceBridge._validate_target(TARGET_CURRENT, 8090)
        SubstanceBridge._validate_target(TARGET_CURRENT, TARGET_AUTO)

    def test_unknown_string_target_rejected(self):
        with self.assertRaises(ValueError):
            SubstanceBridge._validate_target(TARGET_AUTO, "garbage")


class TestResolveConnection(unittest.TestCase):
    """SubstanceBridge._resolve_connection routes target -> connection.

    Tests use fake connection objects to keep the suite Maya/Painter-free.
    """

    def setUp(self):
        # Patch SubstanceConnection.attach at the import site used by the
        # bridge module, not the connection module.
        from mayatk.mat_utils.substance_bridge import _substance_bridge as sb
        self.sb = sb

    def _make_live_conn(self, port=8090):
        class FakeRpc:
            def ping(self, timeout=0.5):
                return True
        class FakeConn:
            def __init__(self):
                self.rpc = FakeRpc()
                self.rpc_port = port
            def is_alive(self):
                return True
        return FakeConn()

    def test_target_new_calls_launch_new(self):
        from unittest.mock import patch
        bridge = SubstanceBridge()
        sentinel = self._make_live_conn()
        with patch.object(bridge, "_launch_new", return_value=sentinel) as mock_launch:
            result = bridge._resolve_connection(TARGET_NEW, ["--mesh", "x.fbx"], False)
            mock_launch.assert_called_once_with(["--mesh", "x.fbx"], False, None)
            self.assertIs(result, sentinel)

    def test_target_new_passes_painter_exe_through(self):
        from unittest.mock import patch
        bridge = SubstanceBridge()
        sentinel = self._make_live_conn()
        with patch.object(bridge, "_launch_new", return_value=sentinel) as mock_launch:
            bridge._resolve_connection(
                TARGET_NEW, [], False, painter_exe="C:/custom/Painter.exe"
            )
            mock_launch.assert_called_once_with([], False, "C:/custom/Painter.exe")

    def test_target_current_with_live_instance_reuses(self):
        bridge = SubstanceBridge()
        existing = self._make_live_conn()
        bridge._instances = [existing]
        # _launch_new must NOT be called.
        from unittest.mock import patch
        with patch.object(bridge, "_launch_new") as mock_launch:
            result = bridge._resolve_connection(TARGET_CURRENT, [], False)
            self.assertIs(result, existing)
            mock_launch.assert_not_called()

    def test_target_current_with_no_instances_errors(self):
        bridge = SubstanceBridge()
        result = bridge._resolve_connection(TARGET_CURRENT, [], False)
        self.assertIsNone(result)

    def test_target_auto_with_live_reuses(self):
        bridge = SubstanceBridge()
        existing = self._make_live_conn()
        bridge._instances = [existing]
        from unittest.mock import patch
        with patch.object(bridge, "_launch_new") as mock_launch:
            result = bridge._resolve_connection(TARGET_AUTO, [], False)
            self.assertIs(result, existing)
            mock_launch.assert_not_called()

    def test_target_auto_with_no_instances_launches(self):
        from unittest.mock import patch
        bridge = SubstanceBridge()
        sentinel = self._make_live_conn()
        with patch.object(bridge, "_launch_new", return_value=sentinel) as mock_launch:
            result = bridge._resolve_connection(TARGET_AUTO, [], False)
            self.assertIs(result, sentinel)
            mock_launch.assert_called_once()

    def test_target_int_attaches_and_registers(self):
        from unittest.mock import patch
        bridge = SubstanceBridge()
        attached = self._make_live_conn(port=9876)
        with patch.object(
            self.sb.SubstanceConnection, "attach", return_value=attached
        ) as mock_attach:
            result = bridge._resolve_connection(9876, [], False)
            mock_attach.assert_called_once_with(port=9876)
            self.assertIs(result, attached)
            # Attached connection must be registered for subsequent "auto" calls.
            self.assertIn(attached, bridge._instances)

    def test_target_int_attach_failure_returns_none(self):
        from unittest.mock import patch
        bridge = SubstanceBridge()
        with patch.object(
            self.sb.SubstanceConnection, "attach",
            side_effect=ConnectionRefusedError("nope"),
        ):
            result = bridge._resolve_connection(9876, [], False)
            self.assertIsNone(result)
            self.assertEqual(bridge._instances, [])


class TestManagedInstanceRegistry(unittest.TestCase):
    """SubstanceBridge.find_live_managed walks MRU and prunes dead."""

    def test_empty_registry_returns_none(self):
        bridge = SubstanceBridge()
        self.assertIsNone(bridge.find_live_managed())
        self.assertEqual(bridge.instances, [])

    def test_dead_entries_are_pruned(self):
        bridge = SubstanceBridge()

        class FakeConn:
            def __init__(self, alive):
                self._alive = alive
                self.rpc = None
                self.rpc_port = 0

            def is_alive(self):
                return self._alive

        bridge._instances = [FakeConn(False), FakeConn(False)]
        result = bridge.find_live_managed()
        self.assertIsNone(result)
        # After the call, the dead entries should be gone.
        self.assertEqual(bridge.instances, [])

    def test_picks_most_recent_live_with_rpc(self):
        bridge = SubstanceBridge()

        class FakeRpc:
            def __init__(self, alive):
                self._alive = alive

            def ping(self, timeout=0.5):
                return self._alive

        class FakeConn:
            def __init__(self, alive, rpc_alive, port):
                self._alive = alive
                self.rpc = FakeRpc(rpc_alive)
                self.rpc_port = port

            def is_alive(self):
                return self._alive

        oldest = FakeConn(True, True, 8090)
        middle_dead = FakeConn(False, False, 8091)
        newest = FakeConn(True, True, 8092)
        bridge._instances = [oldest, middle_dead, newest]

        result = bridge.find_live_managed()
        self.assertIs(result, newest)
        # Dead middle is pruned; oldest + newest survive.
        self.assertEqual(bridge.instances, [oldest, newest])


if __name__ == "__main__":
    unittest.main()
