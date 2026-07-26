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
    _TEMPLATE_DEFAULTS,
)


class TestTemplateDiscovery(unittest.TestCase):
    def test_list_templates_finds_import(self):
        stems = [p.stem for p in SubstanceBridge.list_templates()]
        self.assertIn("import", stems)

    def test_list_templates_skips_underscore_prefixed(self):
        # Sanity: __init__.py is in templates/ but starts with underscore
        # and must not be reported as a user template.
        stems = [p.stem for p in SubstanceBridge.list_templates()]
        self.assertNotIn("__init__", stems)

    def test_list_template_modes_returns_pairs(self):
        pairs = SubstanceBridge.list_template_modes()
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
        path = next(p for p in SubstanceBridge.list_templates() if p.stem == "import")
        meta = SubstanceBridge.parse_template(path)
        self.assertEqual(meta["BRIDGE_MODES"], (SEND_TO,))
        # LAUNCH_ARGS is the minimal flag set current Painter accepts:
        # ``--mesh <fbx>``. The bridge appends ``--mesh-map`` per staged
        # texture and ``--split-by-udim`` (presence flag) at runtime.
        self.assertEqual(meta["LAUNCH_ARGS"], ["--mesh", "__FBX_PATH__"])
        self.assertEqual(meta["RPC_SCRIPT"], "")
        # import.py builds a manifest (folded in from the deleted with_textures
        # template) and embeds Maya-referenced textures into the FBX.
        self.assertEqual(meta["BUILD_MANIFEST"], True)
        self.assertTrue(meta["FBX_OPTIONS"].get("FBXExportEmbeddedTextures"))

    def test_missing_constants_fall_back_to_defaults(self):
        path = self._write("blank.py", '"""empty template"""\n')
        meta = SubstanceBridge.parse_template(path)
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
        meta = SubstanceBridge.parse_template(path)
        self.assertEqual(meta["BRIDGE_MODES"], (SEND_TO,))

    def test_all_invalid_modes_falls_back_to_send_to(self):
        path = self._write("worse.py", 'BRIDGE_MODES = ("invalid",)\n')
        meta = SubstanceBridge.parse_template(path)
        self.assertEqual(meta["BRIDGE_MODES"], (SEND_TO,))

    def test_roundtrip_mode_preserved(self):
        path = self._write(
            "rt.py",
            'BRIDGE_MODES = ("send_to", "roundtrip")\nRPC_SCRIPT = "alg.log(\'hi\')"\n',
        )
        meta = SubstanceBridge.parse_template(path)
        self.assertEqual(meta["BRIDGE_MODES"], (SEND_TO, ROUNDTRIP))
        self.assertIn("alg.log", meta["RPC_SCRIPT"])

    def test_non_literal_value_falls_back(self):
        # An expression (not a literal) should be rejected gracefully.
        path = self._write(
            "expr.py",
            'BAKE_W = 2048\nLAUNCH_ARGS = ["--w", str(BAKE_W)]\n',
        )
        meta = SubstanceBridge.parse_template(path)
        # Non-literal LAUNCH_ARGS -> fall back to default empty list.
        self.assertEqual(meta["LAUNCH_ARGS"], _TEMPLATE_DEFAULTS["LAUNCH_ARGS"])

    def test_wrong_type_falls_back(self):
        path = self._write(
            "wrong_type.py",
            'LAUNCH_ARGS = "not a list"\nRPC_SCRIPT = 42\n',
        )
        meta = SubstanceBridge.parse_template(path)
        self.assertEqual(meta["LAUNCH_ARGS"], _TEMPLATE_DEFAULTS["LAUNCH_ARGS"])
        self.assertEqual(meta["RPC_SCRIPT"], _TEMPLATE_DEFAULTS["RPC_SCRIPT"])

    def test_non_string_launch_arg_entry_falls_back(self):
        # All entries in LAUNCH_ARGS must be strings.
        path = self._write(
            "mixed.py",
            'LAUNCH_ARGS = ["--scale", 1.5]\n',
        )
        meta = SubstanceBridge.parse_template(path)
        self.assertEqual(meta["LAUNCH_ARGS"], _TEMPLATE_DEFAULTS["LAUNCH_ARGS"])

    def test_syntax_error_falls_back(self):
        path = self._write("syntax.py", "this is not python {{[\n")
        meta = SubstanceBridge.parse_template(path)
        # All defaults preserved -- compare to the canonical defaults dict
        # (with BRIDGE_MODES normalized to a tuple) so adding a new field
        # to _TEMPLATE_DEFAULTS doesn't break this test.
        expected = dict(_TEMPLATE_DEFAULTS)
        expected["BRIDGE_MODES"] = (SEND_TO,)
        self.assertEqual(meta, expected)

    def test_missing_file_falls_back(self):
        meta = SubstanceBridge.parse_template(Path(self.tmpdir) / "does_not_exist.py")
        self.assertEqual(meta["BRIDGE_MODES"], (SEND_TO,))

    def test_list_normalized_to_tuple(self):
        # Author might use a list instead of a tuple.
        path = self._write("list_modes.py", 'BRIDGE_MODES = ["send_to"]\n')
        meta = SubstanceBridge.parse_template(path)
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
        meta = SubstanceBridge.parse_template(path)
        self.assertEqual(meta["FBX_OPTIONS"], {})

    def test_dict_value_parsed(self):
        path = self._write(
            "with_opts.py",
            'FBX_OPTIONS = {"FBXExportEmbeddedTextures": True, '
            '"FBXExportTriangulate": True}\n',
        )
        meta = SubstanceBridge.parse_template(path)
        self.assertEqual(
            meta["FBX_OPTIONS"],
            {"FBXExportEmbeddedTextures": True, "FBXExportTriangulate": True},
        )

    def test_wrong_type_falls_back(self):
        path = self._write("bad.py", 'FBX_OPTIONS = "not a dict"\n')
        meta = SubstanceBridge.parse_template(path)
        self.assertEqual(meta["FBX_OPTIONS"], {})

    def test_non_literal_falls_back(self):
        path = self._write("expr.py", "FBX_OPTIONS = dict(a=1)\n")
        meta = SubstanceBridge.parse_template(path)
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
        meta = SubstanceBridge.parse_template(path)
        self.assertEqual(meta["EXPORT_FBX"], True)

    def test_explicit_false_parses(self):
        path = self._write("r.py", "EXPORT_FBX = False\n")
        meta = SubstanceBridge.parse_template(path)
        self.assertEqual(meta["EXPORT_FBX"], False)

    def test_wrong_type_falls_back_to_true(self):
        path = self._write("r.py", 'EXPORT_FBX = "no"\n')
        meta = SubstanceBridge.parse_template(path)
        self.assertEqual(meta["EXPORT_FBX"], True)


class TestBundledTemplates(unittest.TestCase):
    """Sanity checks for the canonical templates the bridge ships with."""

    def test_bundled_templates_full_set(self):
        """Bundled set: import (new project), reimport (update current),
        render (Iray render current), bake_lighting (import + bake Iray
        lighting into diffuse). Guards against accidental drift."""
        stems = sorted(p.stem for p in SubstanceBridge.list_templates())
        self.assertEqual(stems, ["bake_lighting", "import", "reimport", "render"])

    def test_import_template_embeds_textures_and_builds_manifest(self):
        path = next(p for p in SubstanceBridge.list_templates() if p.stem == "import")
        meta = SubstanceBridge.parse_template(path)
        self.assertEqual(meta["BRIDGE_MODES"], (SEND_TO,))
        self.assertTrue(
            meta["FBX_OPTIONS"].get("FBXExportEmbeddedTextures"),
            "import.py must embed textures (replaces with_textures)",
        )
        self.assertTrue(meta["BUILD_MANIFEST"])
        self.assertEqual(meta["TARGET_INSTANCE"], "new")

    def test_reimport_is_send_to_not_roundtrip(self):
        """Reimport is a one-way update of an existing instance, not a
        roundtrip -- nothing comes back from Painter. It dispatches the
        structured ``mesh.reload`` op (not legacy JS), overwrites the
        recorded export path, and declares a manual-fallback hint."""
        path = next(p for p in SubstanceBridge.list_templates() if p.stem == "reimport")
        meta = SubstanceBridge.parse_template(path)
        self.assertEqual(meta["BRIDGE_MODES"], (SEND_TO,))
        self.assertEqual(meta["TARGET_INSTANCE"], "current")
        self.assertEqual(meta["RPC_SCRIPT"], "")
        self.assertEqual(len(meta["RPC_OPS"]), 1)
        op_name, op_kwargs = meta["RPC_OPS"][0]
        self.assertEqual(op_name, "mesh.reload")
        self.assertEqual(op_kwargs["mesh_path"], "__FBX_PATH__")
        self.assertTrue(op_kwargs["preserve_strokes"])
        self.assertTrue(meta["REUSE_RECORDED_EXPORT"])
        self.assertIn("__FBX_PATH__", meta["NO_CONNECTION_HINT"])
        self.assertTrue(meta["EXPORT_FBX"])

    def test_render_template_skips_fbx_and_targets_current(self):
        """render.py asks the running Painter to Iray-render itself; no
        Maya FBX export needed, and it requires a live managed instance."""
        path = next(p for p in SubstanceBridge.list_templates() if p.stem == "render")
        meta = SubstanceBridge.parse_template(path)
        self.assertEqual(meta["BRIDGE_MODES"], (SEND_TO,))
        self.assertEqual(meta["TARGET_INSTANCE"], "current")
        self.assertFalse(meta["EXPORT_FBX"])
        self.assertEqual(meta["LAUNCH_ARGS"], [])
        self.assertIn("exportRenderImage", meta["RPC_SCRIPT"])

    def test_bake_lighting_combines_import_and_iray_render(self):
        """bake_lighting.py = import.py (new project + embed textures) +
        a Painter-side Iray render that lands in the diffuse channel."""
        path = next(
            p for p in SubstanceBridge.list_templates() if p.stem == "bake_lighting"
        )
        meta = SubstanceBridge.parse_template(path)
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
    """parameters.render_cli_context vs render_js_context: the bug we fixed.

    The substance bridge no longer carries its own ``SubstanceParam``
    subclass -- formatting moved to strategy callables in
    :mod:`uitk.bridge.formatters` (``cli_raw`` / ``js_literal``). These
    tests exercise those callables directly with a synthetic spec.
    """

    def _spec(self, kind, default=None):
        from uitk.bridge import AttributeSpec

        return AttributeSpec(key="X", label="X", kind=kind, default=default)

    def test_format_cli_string_is_raw(self):
        """CLI rendering must NOT auto-quote strings -- subprocess would
        otherwise embed literal quotes inside argv values."""
        from uitk.bridge import Formatters
        from mayatk.mat_utils.substance_bridge.parameters import Parameters

        # Unknown keys fall through to str() (the formatter is only
        # consulted for keys present in PARAMS).
        out = Parameters.render_cli_context({"UNKNOWN_KEY": "C:/some/path"})
        self.assertEqual(out["UNKNOWN_KEY"], "C:/some/path")
        # Direct spec test:
        spec = self._spec("path", default="")
        self.assertEqual(
            Formatters.cli_raw(spec, "C:/Painter/template.spp"),
            "C:/Painter/template.spp",
        )

    def test_format_js_string_is_quoted_and_escaped(self):
        from uitk.bridge import Formatters

        spec = self._spec("path", default="")
        # Backslashes doubled, quotes escaped, wrapped in double quotes.
        self.assertEqual(
            Formatters.js_literal(spec, "C:\\foo\\bar"), '"C:\\\\foo\\\\bar"'
        )
        self.assertEqual(Formatters.js_literal(spec, 'say "hi"'), '"say \\"hi\\""')

    def test_format_cli_bool_lowercased(self):
        from uitk.bridge import Formatters

        spec = self._spec("bool", default=False)
        self.assertEqual(Formatters.cli_raw(spec, True), "true")
        self.assertEqual(Formatters.cli_raw(spec, False), "false")

    def test_format_cli_int_plain(self):
        from uitk.bridge import Formatters

        spec = self._spec("int", default=0)
        self.assertEqual(Formatters.cli_raw(spec, 2048), "2048")

    def test_format_cli_file_list_joins_with_pathsep(self):
        import os as _os
        from uitk.bridge import Formatters

        spec = self._spec("file_list", default=[])
        joined = Formatters.cli_raw(spec, ["a.png", "b.png"])
        self.assertEqual(joined, _os.pathsep.join(["a.png", "b.png"]))


class TestAssignedTextureStaging(unittest.TestCase):
    """_stage_assigned_textures copies textures from MatUtils into the output dir."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="substance_textures_test_")
        # Create two fake texture files in a 'src' subdir.
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

    def _patch_mat_utils(self, paths):
        """Replace MatUtils.get_texture_paths with a stub returning *paths*.

        Cleaned up on test teardown via :meth:`addCleanup`.
        """
        from mayatk.mat_utils import _mat_utils

        original = _mat_utils.MatUtils.get_texture_paths
        _mat_utils.MatUtils.get_texture_paths = classmethod(
            lambda cls, **kw: list(paths)
        )
        self.addCleanup(setattr, _mat_utils.MatUtils, "get_texture_paths", original)

    def test_copies_textures_into_output_dir(self):
        self._patch_mat_utils([str(self.src_ao), str(self.src_normal)])
        bridge = SubstanceBridge()
        staged = bridge._stage_assigned_textures(["dummy_obj"], str(self.out_dir))
        self.assertEqual(len(staged), 2)
        for dst in staged:
            self.assertTrue(Path(dst).is_file(), f"staged file missing: {dst}")
            self.assertEqual(Path(dst).parent, self.out_dir)

    def test_missing_source_is_skipped(self):
        self._patch_mat_utils(
            [
                str(self.src_ao),
                str(Path(self.tmpdir) / "does_not_exist.png"),
            ]
        )
        bridge = SubstanceBridge()
        staged = bridge._stage_assigned_textures(["dummy_obj"], str(self.out_dir))
        self.assertEqual(len(staged), 1)

    def test_no_textures_resolves_to_empty_list(self):
        self._patch_mat_utils([])
        bridge = SubstanceBridge()
        staged = bridge._stage_assigned_textures(["dummy_obj"], str(self.out_dir))
        self.assertEqual(staged, [])

    def test_prefix_is_prepended_to_basename(self):
        self._patch_mat_utils([str(self.src_ao)])
        bridge = SubstanceBridge()
        staged = bridge._stage_assigned_textures(
            ["dummy_obj"], str(self.out_dir), prefix="char_"
        )
        self.assertEqual(len(staged), 1)
        self.assertEqual(Path(staged[0]).name, "char_obj_ao.png")
        self.assertTrue(Path(staged[0]).is_file())

    def test_prefix_is_idempotent_when_already_present(self):
        """A source filename that already starts with *prefix* must not get
        the prefix doubled -- the staged file is named ``<prefix><tail>``
        whether or not the source already had it."""
        src = Path(self.tmpdir) / "src" / "char_body_diff.png"
        src.write_bytes(b"already-prefixed")
        self._patch_mat_utils([str(src)])
        bridge = SubstanceBridge()
        staged = bridge._stage_assigned_textures(
            ["dummy_obj"], str(self.out_dir), prefix="char_"
        )
        self.assertEqual(len(staged), 1)
        self.assertEqual(Path(staged[0]).name, "char_body_diff.png")

    def test_empty_prefix_is_no_op(self):
        self._patch_mat_utils([str(self.src_ao)])
        bridge = SubstanceBridge()
        staged = bridge._stage_assigned_textures(
            ["dummy_obj"], str(self.out_dir), prefix=""
        )
        self.assertEqual(Path(staged[0]).name, "obj_ao.png")


class TestRenderTemplateJs(unittest.TestCase):
    """Render template's RPC_SCRIPT must produce valid JS after substitution.

    Specifically, the OUTPUT_DIR internal token must land inside JS quotes
    (template-author-supplied) so the fallback expression
    ``("__OUTPUT_DIR__" + "/painter_render.png")`` doesn't degenerate into
    a bare-identifier parse error like ``C:/path + "/painter_render.png"``.
    """

    def _render(self, params=None):
        bridge = SubstanceBridge()
        path = next(p for p in SubstanceBridge.list_templates() if p.stem == "render")
        meta = SubstanceBridge.parse_template(path)
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
        path = next(
            p for p in SubstanceBridge.list_templates() if p.stem == "bake_lighting"
        )
        meta = SubstanceBridge.parse_template(path)
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

        self.assertGreater(
            len(PARAMS),
            0,
            "parameters.PARAMS must expose at least one knob "
            "or the slot UI shows an empty panel",
        )

    def test_import_template_references_params(self):
        """import.py must reference at least one registered PARAM key so
        the slot panel surfaces the matching widgets (PAINTER_INCLUDE_TEXTURES
        and PAINTER_SPLIT_BY_UDIM are wired post-render rather than baked
        into LAUNCH_ARGS, but appear in the file as comment references)."""
        from mayatk.mat_utils.substance_bridge import parameters as _params

        path = next(
            (p for p in SubstanceBridge.list_templates() if p.stem == "import"),
            None,
        )
        self.assertIsNotNone(path)
        used = _params.Parameters.referenced_keys(path.read_text(encoding="utf-8"))
        self.assertGreater(
            len(used),
            0,
            "import.py should reference at least one PARAMS key so the "
            "slot panel exposes a user-tunable knob",
        )


class TestEndToEndLaunchArgsRendering(unittest.TestCase):
    """End-to-end: load import.py, render LAUNCH_ARGS, verify CLI-clean."""

    def test_import_renders_to_clean_argv(self):
        bridge = SubstanceBridge()
        path = next(p for p in SubstanceBridge.list_templates() if p.stem == "import")
        meta = SubstanceBridge.parse_template(path)

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
            self.assertNotIn('"', arg, f"argv entry has literal quotes: {arg!r}")
            self.assertNotIn("'", arg, f"argv entry has literal quotes: {arg!r}")

        # Current Painter only accepts ``--mesh <fbx>`` here; the dynamic
        # ``--mesh-map`` / ``--split-by-udim`` extensions are appended by
        # ``send()`` after this static render, not by the template.
        self.assertEqual(rendered, ["--mesh", "/tmp/x.fbx"])

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
        for path in SubstanceBridge.list_templates():
            referenced |= _params.Parameters.referenced_keys(
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
        result = SubstanceBridge.resolve_painter_log_path()
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
        meta = SubstanceBridge.parse_template(path)
        self.assertEqual(meta["TARGET_INSTANCE"], TARGET_AUTO)

    def test_explicit_new(self):
        path = self._write("new.py", 'TARGET_INSTANCE = "new"\n')
        meta = SubstanceBridge.parse_template(path)
        self.assertEqual(meta["TARGET_INSTANCE"], TARGET_NEW)

    def test_explicit_current(self):
        path = self._write("cur.py", 'TARGET_INSTANCE = "current"\n')
        meta = SubstanceBridge.parse_template(path)
        self.assertEqual(meta["TARGET_INSTANCE"], TARGET_CURRENT)

    def test_invalid_value_falls_back_to_default(self):
        path = self._write("bad.py", 'TARGET_INSTANCE = "bogus"\n')
        meta = SubstanceBridge.parse_template(path)
        self.assertEqual(meta["TARGET_INSTANCE"], TARGET_AUTO)

    def test_wrong_type_falls_back(self):
        path = self._write("bad.py", "TARGET_INSTANCE = 42\n")
        meta = SubstanceBridge.parse_template(path)
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
        from unittest.mock import patch

        bridge = SubstanceBridge()
        # No managed instance AND the default-port probe finds nothing.
        with patch.object(
            self.sb.SubstanceConnection,
            "attach",
            side_effect=ConnectionRefusedError("nope"),
        ):
            result = bridge._resolve_connection(TARGET_CURRENT, [], False)
        self.assertIsNone(result)

    def test_target_current_discovers_default_port(self):
        """Registry empty, but a Painter with the substance_rpc plugin is
        listening on the default port -- 'current' must attach to it (the
        cross-Maya-session reimport path) and register it for reuse."""
        from unittest.mock import patch

        bridge = SubstanceBridge()
        discovered = self._make_live_conn()
        with patch.object(
            self.sb.SubstanceConnection, "attach", return_value=discovered
        ) as mock_attach:
            result = bridge._resolve_connection(TARGET_CURRENT, [], False)
        self.assertIs(result, discovered)
        mock_attach.assert_called_once_with(
            port=self.sb.DEFAULT_RPC_PORT, verify_timeout=1.0
        )
        self.assertIn(discovered, bridge._instances)

    def test_target_auto_discovers_default_port_before_launching(self):
        from unittest.mock import patch

        bridge = SubstanceBridge()
        discovered = self._make_live_conn()
        with patch.object(
            self.sb.SubstanceConnection, "attach", return_value=discovered
        ), patch.object(bridge, "_launch_new") as mock_launch:
            result = bridge._resolve_connection(TARGET_AUTO, [], False)
        self.assertIs(result, discovered)
        mock_launch.assert_not_called()

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
        with patch.object(
            self.sb.SubstanceConnection,
            "attach",
            side_effect=ConnectionRefusedError("nope"),
        ), patch.object(bridge, "_launch_new", return_value=sentinel) as mock_launch:
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
            self.sb.SubstanceConnection,
            "attach",
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


class TestRpcOpsField(unittest.TestCase):
    """RPC_OPS template field parsing + normalization."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="substance_rpcops_test_")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, name, body):
        path = Path(self.tmpdir) / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_default_is_empty(self):
        path = self._write("blank.py", '"""empty"""\n')
        meta = SubstanceBridge.parse_template(path)
        self.assertEqual(meta["RPC_OPS"], [])

    def test_valid_pairs_parse_and_normalize_to_tuples(self):
        path = self._write(
            "ops.py",
            'RPC_OPS = [["mesh.reload", {"mesh_path": "__FBX_PATH__"}], '
            '("system.ping", {})]\n',
        )
        meta = SubstanceBridge.parse_template(path)
        self.assertEqual(
            meta["RPC_OPS"],
            [
                ("mesh.reload", {"mesh_path": "__FBX_PATH__"}),
                ("system.ping", {}),
            ],
        )

    def test_malformed_entry_voids_the_field(self):
        # A half-broken op list must not dispatch a partial sequence.
        path = self._write(
            "bad.py",
            'RPC_OPS = [("mesh.reload", {"mesh_path": "x"}), "not_a_pair"]\n',
        )
        meta = SubstanceBridge.parse_template(path)
        self.assertEqual(meta["RPC_OPS"], [])

    def test_wrong_type_falls_back(self):
        path = self._write("bad.py", 'RPC_OPS = "mesh.reload"\n')
        meta = SubstanceBridge.parse_template(path)
        self.assertEqual(meta["RPC_OPS"], [])


class TestReimportSupportFields(unittest.TestCase):
    """REUSE_RECORDED_EXPORT + NO_CONNECTION_HINT parsing."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="substance_reimport_fields_")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, name, body):
        path = Path(self.tmpdir) / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_defaults(self):
        path = self._write("blank.py", '"""empty"""\n')
        meta = SubstanceBridge.parse_template(path)
        self.assertFalse(meta["REUSE_RECORDED_EXPORT"])
        self.assertEqual(meta["NO_CONNECTION_HINT"], "")

    def test_explicit_values_parse(self):
        path = self._write(
            "r.py",
            'REUSE_RECORDED_EXPORT = True\nNO_CONNECTION_HINT = "do it by hand"\n',
        )
        meta = SubstanceBridge.parse_template(path)
        self.assertTrue(meta["REUSE_RECORDED_EXPORT"])
        self.assertEqual(meta["NO_CONNECTION_HINT"], "do it by hand")


class TestRenderRpcOps(unittest.TestCase):
    """_render_rpc_ops substitutes __KEY__ only inside string kwargs."""

    def test_string_kwargs_substituted_others_untouched(self):
        rendered = SubstanceBridge._render_rpc_ops(
            [
                (
                    "mesh.reload",
                    {
                        "mesh_path": "__FBX_PATH__",
                        "preserve_strokes": True,
                        "count": 3,
                    },
                )
            ],
            {"FBX_PATH": "C:/tmp/scene.fbx"},
        )
        self.assertEqual(
            rendered,
            [
                (
                    "mesh.reload",
                    {
                        "mesh_path": "C:/tmp/scene.fbx",
                        "preserve_strokes": True,
                        "count": 3,
                    },
                )
            ],
        )


class TestTargetNarrowing(unittest.TestCase):
    """_preflight narrows the default 'auto' target to the template's
    TARGET_INSTANCE, so a 'current'-only template (reimport) can never
    silently launch a fresh Painter -- the originally reported bug."""

    def _preflight(self, template, target=TARGET_AUTO):
        import pythontk as ptk

        bridge = SubstanceBridge()
        request = ptk.HandoffRequest(
            template=template,
            mode=SEND_TO,
            params={},
            extras={"target": target},
        )
        ok = bridge._preflight(None, request)
        return ok, request

    def test_auto_narrows_to_current_for_reimport(self):
        ok, request = self._preflight("reimport")
        self.assertTrue(ok)
        self.assertEqual(request.get("target"), TARGET_CURRENT)

    def test_auto_narrows_to_new_for_import(self):
        ok, request = self._preflight("import")
        self.assertTrue(ok)
        self.assertEqual(request.get("target"), TARGET_NEW)

    def test_explicit_int_port_survives_narrowing(self):
        ok, request = self._preflight("reimport", target=9876)
        self.assertTrue(ok)
        self.assertEqual(request.get("target"), 9876)


class TestExportPathRecording(unittest.TestCase):
    """_record_export_path / _recorded_export_path round-trip through the
    scene's fileInfo -- the mechanism that lets reimport overwrite the
    exact file Painter's project points at, across Maya sessions.

    ``cmds`` is faked on the *bridge module's* namespace only (create=True
    -- the venv has no maya), never on the maya package itself.
    """

    def _fake_cmds(self, store):
        class FakeCmds:
            @staticmethod
            def fileInfo(key, value=None, query=False):
                if query:
                    return [store[key]] if key in store else []
                store[key] = value

        return FakeCmds()

    def _patched(self, store):
        from unittest.mock import patch
        from mayatk.mat_utils.substance_bridge import _substance_bridge as sb

        return patch.object(sb, "cmds", self._fake_cmds(store), create=True)

    def test_round_trip_normalizes_backslashes(self):
        store = {}
        with self._patched(store):
            SubstanceBridge._record_export_path("C:\\tmp\\scene.fbx")
            self.assertEqual(
                SubstanceBridge._recorded_export_path(), "C:/tmp/scene.fbx"
            )
        # Stored under the documented key, forward-slashed at write time.
        self.assertEqual(
            store[SubstanceBridge.EXPORT_RECORD_KEY], "C:/tmp/scene.fbx"
        )

    def test_no_record_returns_none(self):
        with self._patched({}):
            self.assertIsNone(SubstanceBridge._recorded_export_path())

    def test_helpers_survive_missing_maya(self):
        # In this venv the module-level ``from maya import cmds`` failed,
        # so the name doesn't exist -- both helpers must degrade to no-op
        # rather than raise (recording is best-effort by contract).
        self.assertIsNone(SubstanceBridge._recorded_export_path())
        SubstanceBridge._record_export_path("C:/tmp/x.fbx")  # must not raise


class TestDeliverNoConnectionFallback(unittest.TestCase):
    """When a hint-declaring template can't reach Painter, _deliver keeps
    the produced FBX (already overwritten on disk) and logs the manual
    steps instead of returning None."""

    def _deliver(self, template):
        import pythontk as ptk
        from unittest.mock import patch

        bridge = SubstanceBridge()
        path = next(
            p for p in SubstanceBridge.list_templates() if p.stem == template
        )
        meta = SubstanceBridge.parse_template(path)
        payload = ptk.Payload(
            primary="C:/tmp/scene.fbx",
            extras={
                "meta": meta,
                "manifest_path": "C:/tmp/scene.materials.json",
                "output_dir": "C:/tmp",
                "staged_textures": [],
                "referenced": set(),
            },
        )
        request = ptk.HandoffRequest(
            template=template,
            mode=SEND_TO,
            params={},
            extras={"target": TARGET_CURRENT},
        )
        with patch.object(bridge, "ensure_rpc_plugin"), patch.object(
            bridge, "_resolve_connection", return_value=None
        ):
            return bridge._deliver(payload, request)

    def test_reimport_returns_partial_result_with_hint(self):
        result = self._deliver("reimport")
        self.assertIsNotNone(result)
        self.assertIsNone(result["connection"])
        self.assertFalse(result["delivered"])
        self.assertEqual(result["fbx"], "C:/tmp/scene.fbx")

    def test_hintless_template_still_fails_hard(self):
        # import.py declares no hint -- an unresolvable connection is a
        # real failure there (nothing useful happened yet for the user).
        result = self._deliver("import")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
