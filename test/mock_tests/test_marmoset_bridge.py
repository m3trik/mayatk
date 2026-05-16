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
    list_template_modes,
    template_modes,
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

                with unittest.mock.patch(
                    "mayatk.mat_utils.marmoset_bridge._marmoset_bridge.AppLauncher"
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
                        "__FBX_PATH__",
                        "__MANIFEST_PATH__",
                        "__OUTPUT_DIR__",
                        "__SAVE_PATH__",
                        "__SHOULD_QUIT__",
                    ):
                        self.assertNotIn(fixed, body, f"{fixed} not substituted")

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
        templates = sorted(p.stem for p in _TEMPLATE_DIR.glob("*.py"))
        self.assertTrue(templates, "No bundled templates found.")

        bridge = MarmosetBridge()
        for stem in templates:
            with self.subTest(template=stem):
                rendered = bridge.render_template(
                    template=stem,
                    fbx_path="/tmp/a.fbx",
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
            fbx_path="/tmp/a.fbx",
            manifest_path="/tmp/a.materials.json",
            output_dir="/tmp/out",
            headless=False,
            params={"BAKE_WIDTH": 4096, "BAKE_BITS": 16, "MAP_NORMAL": False},
        )
        self.assertIn("BAKE_WIDTH = 4096", rendered)
        self.assertIn("BAKE_BITS = 16", rendered)
        self.assertIn("MAP_NORMAL = False", rendered)

    def test_render_template_unknown_name_returns_none(self):
        """Unknown template name surfaces a None return, not an exception."""
        bridge = MarmosetBridge()
        self.assertIsNone(
            bridge.render_template(
                template="does_not_exist",
                fbx_path="/tmp/a.fbx",
                manifest_path="/tmp/a.materials.json",
                output_dir="/tmp/out",
            )
        )

    # ------------------------------------------------------------------
    # BRIDGE_MODES parsing
    # ------------------------------------------------------------------

    def test_bridge_modes_per_template(self):
        """Each bundled template declares the modes we expect."""
        modes = {p.stem: template_modes(p) for p in _TEMPLATE_DIR.glob("*.py")}
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
            fbx_path="/tmp/x.fbx",
            manifest_path="/tmp/x.materials.json",
            output_dir="/tmp/out",
        )
        roundtrip = bridge.render_template(
            template="bake",
            mode=ROUNDTRIP,
            fbx_path="/tmp/x.fbx",
            manifest_path="/tmp/x.materials.json",
            output_dir="/tmp/out",
        )
        self.assertIn("SHOULD_QUIT = False", send_to)
        self.assertIn("SHOULD_QUIT = True", roundtrip)
        # save path only populated when headless
        self.assertIn('SAVE_PATH = r""', send_to)
        self.assertIn("x.tbscene", roundtrip)

    def test_parameters_referenced_keys(self):
        """referenced_keys returns only the registered placeholders a template uses."""
        bake = (_TEMPLATE_DIR / "bake.py").read_text(encoding="utf-8")
        used = _params.referenced_keys(bake)
        # bake.py exposes the bake-* and MAP_* + high/low knobs.
        for must_be_present in (
            "BAKE_WIDTH",
            "BAKE_HEIGHT",
            "BAKE_BITS",
            "MAP_NORMAL",
            "HIGH_SUFFIX",
        ):
            self.assertIn(must_be_present, used)
        # SKY_PRESET belongs to lookdev, not bake.
        self.assertNotIn("SKY_PRESET", used)


if __name__ == "__main__":
    unittest.main()
