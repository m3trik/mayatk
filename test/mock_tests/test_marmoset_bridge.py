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
    MarmosetEngine,
    SEND_TO,
    ROUNDTRIP,
    _TEMPLATE_DIR,
)

# Log helpers are bundled in the marmoset_bridge subpackage alongside the engine.
from mayatk.mat_utils.marmoset_bridge.toolbag_log import ToolbagLog
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
        templates = sorted(p.stem for p in MarmosetEngine.list_templates())
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
        """User-supplied params override registry defaults in the rendered body.

        ``AUTO_MAPS`` off, because it is on by default and deliberately
        supersedes the per-map toggles -- that interaction is pinned
        separately below; this is about plain override plumbing.
        """
        bridge = MarmosetBridge()
        rendered = bridge.render_template(
            template="bake",
            model_path="/tmp/a.fbx",
            manifest_path="/tmp/a.materials.json",
            output_dir="/tmp/out",
            headless=False,
            params={"BAKE_SIZE": 2048, "MAP_NORMAL": False, "AUTO_MAPS": False},
        )
        self.assertIn("BAKE_SIZE = 2048", rendered)
        self.assertIn("MAP_NORMAL = False", rendered)

    def test_managed_bake_values_ignore_user_overrides(self):
        """BAKE_PADDING / BAKE_BITS are DERIVED, not user-tunable.

        Padding comes from the map size via pythontk's UV-padding primitive
        (the ecosystem's one shell/edge-spacing rule) and bit depth from the
        per-map-type output templates, so a stale caller-supplied value must
        not reach the rendered script -- that is the whole point of taking
        the widgets away.
        """
        import pythontk as ptk

        bridge = MarmosetBridge()
        rendered = bridge.render_template(
            template="bake",
            model_path="/tmp/a.fbx",
            manifest_path="/tmp/a.materials.json",
            output_dir="/tmp/out",
            headless=False,
            params={"BAKE_SIZE": 2048, "BAKE_BITS": 16, "BAKE_PADDING": 999},
        )
        self.assertIn(
            f"BAKE_PADDING = {ptk.MathUtils.calculate_uv_padding(2048)!r}", rendered
        )
        self.assertNotIn("BAKE_PADDING = 999", rendered)
        self.assertNotIn("BAKE_BITS = 16", rendered)

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
        modes = {
            p.stem: MarmosetEngine.template_modes(p)
            for p in MarmosetEngine.list_templates()
        }
        self.assertEqual(modes.get("import"), (SEND_TO,))
        self.assertEqual(modes.get("lookdev"), (SEND_TO,))
        # bake supports both -- order matters: it's the source of truth for
        # the combo's expansion.
        self.assertEqual(modes.get("bake"), (SEND_TO, ROUNDTRIP))

    def test_list_template_modes_expands_dual_mode(self):
        """list_template_modes() yields one (stem, mode) per declared mode."""
        pairs = MarmosetEngine.list_template_modes()
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
            out = MarmosetBridge.build_bake_pairs_manifest(
                ["|bake_high", "|bake_low", "|loose_low"], "_high", "_low"
            )

        # bake_high contributes mesh_a + mesh_b -> 'source' (parent chain).
        # bake_low contributes mesh_c -> 'target' (parent chain).
        # loose_low has _low in its OWN name -> 'target' (own-name match).
        # The values are the sidecar's wire format, read verbatim by the
        # Toolbag-side ``split_source_target`` -- an unrecognised word there
        # is silently ignored, so they're asserted literally.
        self.assertEqual(
            out,
            {
                "mesh_a": "source",
                "mesh_b": "source",
                "mesh_c": "target",
                "loose_low": "target",
            },
        )

    def test_build_bake_pairs_manifest_include_children_off_skips_group_meshes(self):
        """``include_children=False`` classifies by the mesh's OWN name only.

        Same scene as above: with the ancestor walk off, only the mesh whose
        own name carries a suffix is recorded; the group's children drop out
        of the sidecar and fall through to the Toolbag-side rules.
        """
        relatives = {
            "|bake_high": ["|bake_high|mesh_a"],
            "|loose_low": [],
            "|bake_high|mesh_a:shapes": ["|bake_high|mesh_a|shape_a"],
            "|loose_low:shapes": ["|loose_low|loose_low_shape"],
            "|bake_high:shapes": [],
            "|bake_high|mesh_a:parent": ["|bake_high"],
            "|bake_high:parent": [],
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
            out = MarmosetBridge.build_bake_pairs_manifest(
                ["|bake_high", "|loose_low"], "_high", "_low", include_children=False
            )

        self.assertEqual(out, {"loose_low": "target"})

    def test_build_bake_pairs_manifest_returns_empty_when_no_suffixes(self):
        """If both suffixes are blank, no classification is possible.
        The helper must return an empty dict without scanning the scene."""
        # No listRelatives calls expected -- but we patch anyway to confirm
        # the helper bails before any DAG work.
        with unittest.mock.patch.object(
            mock_cmds, "listRelatives", return_value=[]
        ) as mock_lr:
            out = MarmosetBridge.build_bake_pairs_manifest(["|anything"], "", "")
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

            snap = MarmosetEngine._snapshot_outputs(tmp)
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

            snap = MarmosetEngine._snapshot_outputs(tmp, since=cutoff)
            self.assertEqual(
                {os.path.basename(p) for p in snap},
                {"bake_Normal.psd"},
                "snapshot with since= filter must include overwrites and "
                "exclude untouched pre-existing files",
            )

            # Sanity: the unfiltered call still returns both.
            full = MarmosetEngine._snapshot_outputs(tmp)
            self.assertEqual(
                {os.path.basename(p) for p in full},
                {"bake_Normal.psd", "irrelevant_unchanged.psd"},
            )

    # ------------------------------------------------------------------
    # Post-bake rewire -- the paths that land in fileTextureName
    # ------------------------------------------------------------------

    def _rewire(self, outputs, output_dir="", assignments=None):
        """Run _assign_baked_materials with create_network captured.

        Returns ``(texture_lists_passed_to_create_network, warnings)``.
        """
        bridge = MarmosetBridge()
        seen, warnings = [], []
        bridge.logger = unittest.mock.MagicMock()
        bridge.logger.warning.side_effect = warnings.append

        from mayatk.mat_utils import game_shader

        def _fake_create_network(self_, textures, **kwargs):
            seen.append(list(textures))
            return "baked_SG"

        # patch.object, not a bare ``return_value =``: reset_mock() in setUp
        # does not clear configured returns, so assigning them would leak into
        # every test that sorts after these.
        with unittest.mock.patch.object(
            game_shader.GameShader, "create_network", _fake_create_network
        ), unittest.mock.patch.object(
            mock_cmds, "objExists", return_value=True
        ), unittest.mock.patch.object(
            mock_cmds, "nodeType", return_value="StingrayPBS"
        ):
            bridge._assign_baked_materials(
                outputs,
                assignments if assignments is not None else {"FLOOR_mat": ["|pCube1"]},
                output_dir=output_dir,
            )
        return seen, warnings

    def test_rewire_normalizes_windows_separators(self):
        """Baked paths reach ``fileTextureName`` forward-slashed.

        ``_relocate_outputs`` builds its results with ``os.path.join``, so on
        Windows they come back backslashed while every other stored texture
        path in this package is forward-slashed (manifest build, sourceimages
        copy, remap keys). Mixing the two makes later path comparisons miss.
        """
        outputs = [
            r"C:\proj\sourceimages\bake\FLOOR_mat_Base_Color.png",
            r"C:\proj\sourceimages\bake\FLOOR_mat_Normal.png",
        ]
        seen, _ = self._rewire(outputs)
        self.assertTrue(seen, "create_network was never called")
        for path in seen[0]:
            self.assertNotIn("\\", path, path)
        self.assertIn("C:/proj/sourceimages/bake/FLOOR_mat_Base_Color.png", seen[0])

    def test_rewire_warns_when_a_map_is_still_in_the_bake_scratch(self):
        """An unverified copy keeps its scratch original -- say so before wiring.

        ``_relocate_outputs`` falls back to the scratch path when a copy can't
        be size-verified. That store is age-swept on a later bake, so a
        material wired to it loses the texture with no further warning.
        """
        outputs = [
            r"C:\proj\out\FLOOR_mat_Base_Color.png",
            r"C:\Temp\marmoset_bake_1234\FLOOR_mat_Normal.png",  # scratch fallback
        ]
        seen, warnings = self._rewire(outputs, output_dir=r"C:\proj\out")
        joined = "\n".join(warnings)
        self.assertIn("FLOOR_mat_Normal.png", joined)
        self.assertIn("scratch", joined.lower())
        # Still wired -- a map on disk beats no map; the warning is the point.
        self.assertEqual(len(seen[0]), 2)

    def test_rewire_is_quiet_when_every_map_landed_in_the_output_dir(self):
        outputs = [
            r"C:\proj\out\FLOOR_mat_Base_Color.png",
            r"C:\proj\out\FLOOR_mat_Normal.png",
        ]
        _, warnings = self._rewire(outputs, output_dir=r"C:\proj\out")
        self.assertEqual([w for w in warnings if "scratch" in w.lower()], [])

    # ------------------------------------------------------------------
    # AUTO_MAPS -- the roster comes from the source materials' textures
    # ------------------------------------------------------------------

    @staticmethod
    def _manifest_file(materials):
        fd, path = tempfile.mkstemp(suffix=".materials.json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            import json

            json.dump({"materials": materials}, fh)
        return path

    def _render_auto(self, materials, **params):
        path = self._manifest_file(materials)
        try:
            return MarmosetBridge().render_template(
                template="bake",
                model_path="/tmp/a.fbx",
                manifest_path=path,
                output_dir="/tmp/out",
                headless=False,
                params={"AUTO_MAPS": True, **params},
            )
        finally:
            os.remove(path)

    def test_auto_maps_enables_only_the_transfer_maps_a_source_textures(self):
        rendered = self._render_auto(
            {"mat": {"baseColor": "c:/t/mat_BaseColor.png", "normal": "c:/t/mat_N.png"}}
        )
        self.assertIn("MAP_ALBEDO = True", rendered)
        # No roughness / metalness / emissive texture on any source material.
        self.assertIn("MAP_ROUGHNESS = False", rendered)
        self.assertIn("MAP_METALNESS = False", rendered)
        self.assertIn("MAP_EMISSIVE = False", rendered)

    def test_auto_maps_always_keeps_the_geometry_maps(self):
        """Normal / AO bake from the source MESH, so no source texture gates them."""
        rendered = self._render_auto({"mat": {}})
        self.assertIn("MAP_NORMAL = True", rendered)
        self.assertIn("MAP_AO = True", rendered)

    def test_auto_maps_overrides_the_per_map_toggles(self):
        """The widgets are greyed while Auto is on -- their values must not win."""
        rendered = self._render_auto(
            {"mat": {"roughness": "c:/t/mat_Roughness.png"}},
            MAP_ALBEDO=True,
            MAP_EMISSIVE=True,
        )
        self.assertIn("MAP_ROUGHNESS = True", rendered)
        self.assertIn("MAP_ALBEDO = False", rendered)
        self.assertIn("MAP_EMISSIVE = False", rendered)

    def test_auto_maps_drives_the_derived_bit_depth(self):
        """BAKE_BITS reads the ENABLED maps, so auto has to resolve first."""
        import pythontk as ptk

        from mayatk.mat_utils.marmoset_bridge import template_params

        rendered = self._render_auto({"mat": {"baseColor": "c:/t/m_BaseColor.png"}})
        roster = template_params.TemplateParams.derive_auto_maps(
            {"materials": {"mat": {"baseColor": "c:/t/m_BaseColor.png"}}}
        )
        expected = max(
            ptk.OutputTemplates.resolve(
                template_params.TemplateParams.MAP_KEY_TYPES[k]
            ).bit_depth
            for k, on in roster.items()
            if on
        )
        self.assertIn(f"BAKE_BITS = {expected}", rendered)

    def _render_capturing_log(self, params, level="info"):
        """Render a bake and return the engine's log lines at *level*."""
        path = self._manifest_file({"mat": {"baseColor": "c:/t/m_BaseColor.png"}})
        lines = []
        bridge = MarmosetBridge()
        with unittest.mock.patch.object(
            bridge.deliverer.logger, level, side_effect=lines.append
        ):
            try:
                bridge.render_template(
                    template="bake",
                    model_path="/tmp/a.fbx",
                    manifest_path=path,
                    output_dir="/tmp/out",
                    headless=False,
                    params=params,
                )
            finally:
                os.remove(path)
        return lines

    def test_auto_maps_says_the_per_map_toggles_are_not_read(self):
        """Auto is ON by default, so a caller can supersede itself.

        The panel greys those rows, which says it on screen; a scripted
        ``send(params={"MAP_NORMAL": False})`` has only the log, so the one
        line auto always emits has to carry the disclosure.
        """
        joined = " ".join(self._render_capturing_log({"MAP_NORMAL": False}))
        self.assertIn("not read", joined)
        self.assertIn("AUTO_MAPS=False", joined)

    def test_auto_maps_does_not_warn_about_the_toggles_it_supersedes(self):
        """A panel send reports every parameter it holds, visible or not.

        Naming each superseded toggle would therefore fire on almost every
        ordinary bake (a source with no roughness texture vs the widget's
        default-on Roughness), turning the log's warnings into noise on the
        primary path.
        """
        warnings = self._render_capturing_log(
            {"MAP_NORMAL": False, "MAP_ROUGHNESS": True, "MAP_EMISSIVE": True},
            level="warning",
        )
        self.assertEqual(warnings, [])

    def test_auto_maps_off_leaves_the_widget_roster_alone(self):
        rendered = MarmosetBridge().render_template(
            template="bake",
            model_path="/tmp/a.fbx",
            manifest_path="/tmp/missing.materials.json",
            output_dir="/tmp/out",
            headless=False,
            params={"AUTO_MAPS": False, "MAP_EMISSIVE": True, "MAP_AO": False},
        )
        self.assertIn("MAP_EMISSIVE = True", rendered)
        self.assertIn("MAP_AO = False", rendered)

    def test_auto_maps_without_a_readable_manifest_bakes_geometry_only(self):
        """Silently falling back to the fixed roster would contradict the toggle."""
        rendered = MarmosetBridge().render_template(
            template="bake",
            model_path="/tmp/a.fbx",
            manifest_path="/tmp/does_not_exist.materials.json",
            output_dir="/tmp/out",
            headless=False,
            params={"AUTO_MAPS": True, "MAP_ALBEDO": True},
        )
        self.assertIn("MAP_NORMAL = True", rendered)
        self.assertIn("MAP_ALBEDO = False", rendered)

    # ------------------------------------------------------------------
    # Re-bake: overwrite, don't accumulate
    # ------------------------------------------------------------------

    def test_baked_material_name_is_idempotent(self):
        """A re-bake reads its set names off the PREVIOUS bake's materials.

        Appending unconditionally produced mat_BAKED_BAKED_BAKED -- a new
        material AND a new set of map files per bake.
        """
        self.assertEqual(MarmosetBridge.baked_material_name("mat"), "mat_BAKED")
        self.assertEqual(MarmosetBridge.baked_material_name("mat_BAKED"), "mat_BAKED")
        self.assertEqual(
            MarmosetBridge.baked_material_name("mat_BAKED_BAKED_BAKED"), "mat_BAKED"
        )

    def test_baked_material_name_sanitizes_but_keeps_case(self):
        self.assertEqual(
            MarmosetBridge.baked_material_name("standardSurface1"),
            "standardSurface1_BAKED",
        )

    def test_shader_type_follows_the_material_already_on_the_meshes(self):
        """A Stingray scene must come back Stingray, not retyped to a default."""
        with unittest.mock.patch.object(mock_cmds, "objExists", return_value=True):
            for node_type, expected in (
                ("StingrayPBS", "stingray"),
                ("standardSurface", "standard_surface"),
                ("openPBRSurface", "open_pbr"),
                ("lambert", "stingray"),  # no GameShader equivalent -> default
            ):
                with unittest.mock.patch.object(
                    mock_cmds, "nodeType", return_value=node_type
                ):
                    self.assertEqual(
                        MarmosetBridge._shader_type_of("mat"), expected, node_type
                    )

    def test_shader_type_falls_back_when_the_material_is_gone(self):
        with unittest.mock.patch.object(mock_cmds, "objExists", return_value=False):
            self.assertEqual(MarmosetBridge._shader_type_of("mat"), "stingray")

    @staticmethod
    def _retire_env(state):
        """cmds stand-ins for ``_retire_previous_network``.

        ``listConnections`` answers both questions the method asks: the
        rebuild's surface shader (which the reclaim renames) and the retired
        material's shading engines (which go with it).
        """

        def _conn(node, **_kwargs):
            if str(node).endswith(".surfaceShader"):
                return ["mat_BAKED"] if state.get("renamed") else ["mat_BAKED1"]
            return ["mat_BAKEDSG"]

        return _conn

    def test_retiring_the_previous_bake_frees_its_name_for_the_rebuild(self):
        bridge = MarmosetBridge()
        state = {"renamed": False}

        def _claim(shading_group, desired):
            state["renamed"] = True
            return shading_group

        with unittest.mock.patch.multiple(
            mock_cmds,
            listConnections=unittest.mock.DEFAULT,
            objExists=unittest.mock.DEFAULT,
            delete=unittest.mock.DEFAULT,
        ) as m, unittest.mock.patch(
            "mayatk.mat_utils._mat_utils.MatUtils.claim_material_name",
            side_effect=_claim,
        ) as claim:
            m["listConnections"].side_effect = self._retire_env(state)
            m["objExists"].return_value = True
            name = bridge._retire_previous_network(
                "mat_BAKED", "rebuiltSG", "mat_BAKED"
            )
            m["delete"].assert_called_once_with(["mat_BAKED", "mat_BAKEDSG"])
            claim.assert_called_once_with("rebuiltSG", "mat_BAKED")
            self.assertEqual(name, "mat_BAKED")

    def test_an_undeletable_previous_leaves_the_rebuild_under_its_own_name(self):
        """A locked/referenced material must not abort the remaining sets."""
        bridge = MarmosetBridge()
        with unittest.mock.patch.multiple(
            mock_cmds,
            listConnections=unittest.mock.DEFAULT,
            objExists=unittest.mock.DEFAULT,
            delete=unittest.mock.DEFAULT,
        ) as m, unittest.mock.patch(
            "mayatk.mat_utils._mat_utils.MatUtils.claim_material_name"
        ) as claim:
            m["listConnections"].side_effect = self._retire_env({})
            m["objExists"].return_value = True
            m["delete"].side_effect = RuntimeError("locked")
            name = bridge._retire_previous_network(
                "mat_BAKED", "rebuiltSG", "mat_BAKED"
            )
            claim.assert_not_called()
            self.assertEqual(name, "mat_BAKED1")

    def test_a_previous_that_no_longer_exists_is_a_quiet_no_op(self):
        bridge = MarmosetBridge()
        with unittest.mock.patch.multiple(
            mock_cmds,
            listConnections=unittest.mock.DEFAULT,
            objExists=unittest.mock.DEFAULT,
            delete=unittest.mock.DEFAULT,
        ) as m:
            m["listConnections"].side_effect = self._retire_env({})
            m["objExists"].return_value = False
            name = bridge._retire_previous_network(
                "mat_BAKED", "rebuiltSG", "mat_BAKED"
            )
            m["delete"].assert_not_called()
            self.assertEqual(name, "mat_BAKED1")

    def test_widget_and_value_registries_agree(self):
        """The two default registries must not drift.

        ``template_params.DEFAULTS`` is what a HEADLESS ``send()`` renders
        with; ``parameters.PARAMS[k].default`` is what the panel's widget
        opens on. They are separate on purpose (values vs Qt specs) but they
        describe the same knob, so a rename or a changed default has to land
        in both -- otherwise the panel and a scripted send quietly disagree
        and only one of them is ever read in any given run.

        ``action`` rows are exempt: they carry no value (their spec default
        is None), and their DEFAULTS entry exists only so the template's
        echo token still substitutes. ``MANAGED_KEYS`` are exempt too -- the
        host bridge fills them in per send, so there is no knob to agree about.
        """
        from mayatk.mat_utils.marmoset_bridge import template_params

        managed = set(template_params.TemplateParams.MANAGED_KEYS)
        values = {k: v for k, v in template_params.DEFAULTS.items() if k not in managed}
        specs = _params.Parameters.PARAMS
        self.assertEqual(
            set(values),
            set(specs),
            "every token needs both a default value and a widget spec",
        )
        self.assertFalse(
            managed & set(specs),
            "a managed value must not also be a widget -- the panel would "
            "overwrite what the host measured",
        )
        drift = {
            key: (values[key], spec.default)
            for key, spec in specs.items()
            if spec.kind != "action" and values[key] != spec.default
        }
        self.assertEqual(drift, {}, f"default drift between the registries: {drift}")

    def test_auto_maps_greys_exactly_the_toggles_it_replaces(self):
        """The governed list and the roster must describe the same set.

        The registry greys every ``MAP_*`` row; ``derive_auto_maps`` resolves
        every key in ``MAP_KEY_TYPES``. A toggle in one list but not the other
        is either greyed while still steering the bake, or live while being
        overridden -- both silent, and both indistinguishable from a working
        panel until someone compares a bake to what the checkboxes said.
        """
        from mayatk.mat_utils.marmoset_bridge import template_params

        governed = {
            key: set(gov)
            for key, gov, _reason in _params.Parameters.SUPERSESSIONS
        }
        self.assertEqual(
            governed["AUTO_MAPS"], set(template_params.TemplateParams.MAP_KEY_TYPES)
        )
        self.assertEqual(governed["AUTO_CAGE"], {"CAGE_OFFSET"})

    def test_cage_offset_range_survives_a_centimetre_scene(self):
        """A distance in SCENE UNITS must not carry a metres-scale clamp.

        A 1.0 maximum made the control unable to reach source geometry
        standing 4-9 cm off its target in a centimetre scene (OFFICE_ENV):
        the detail was absent from the bake and the one control that looked
        responsible was already at its maximum.
        """
        spec = _params.Parameters.PARAMS["CAGE_OFFSET"]
        self.assertGreaterEqual(spec.maximum, 1000.0)
        self.assertEqual(spec.minimum, 0.0)

    def test_the_auto_toggles_are_on_by_default(self):
        """Both defaults answer a question the scene can answer for itself.

        AUTO_CAGE: no fixed offset is right in both a centimetre and a metre
        scene. AUTO_MAPS: a fixed roster bakes flat files for channels the
        source has no texture in and misses ones it does carry.
        """
        from mayatk.mat_utils.marmoset_bridge import template_params

        for key in ("AUTO_CAGE", "AUTO_MAPS"):
            with self.subTest(key=key):
                self.assertTrue(_params.Parameters.PARAMS[key].default)
                self.assertTrue(template_params.DEFAULTS[key])

    def test_supersession_keys_are_all_registered_params(self):
        for trigger, governed, _reason in _params.Parameters.SUPERSESSIONS:
            self.assertIn(trigger, _params.Parameters.PARAMS)
            for key in governed:
                self.assertIn(key, _params.Parameters.PARAMS)

    def test_parameters_referenced_keys(self):
        """referenced_keys returns only the registered placeholders a template uses."""
        bake = (_TEMPLATE_DIR / "bake.py").read_text(encoding="utf-8")
        used = _params.Parameters.referenced_keys(bake)
        # bake.py exposes the bake-* and MAP_* + source/target knobs.
        for must_be_present in (
            "BAKE_SIZE",
            "BAKE_SAMPLES",
            "MAP_NORMAL",
            "HIGH_SUFFIX",
            "LOW_SUFFIX",
            "SUFFIX_INCLUDE_CHILDREN",
            # Host-side knobs: AUTO_MAPS is resolved before substitution and
            # only ECHOED in the template's comment header -- that echo is the
            # single reason its row appears at all, so drop it and the toggle
            # silently disappears from the panel.
            "AUTO_MAPS",
            "AUTO_CAGE",
        ):
            self.assertIn(must_be_present, used)
        # SKY_PRESET belongs to lookdev, not bake.
        self.assertNotIn("SKY_PRESET", used)
        # BAKE_PADDING / BAKE_BITS ARE referenced by the template but are
        # deliberately NOT registered params -- they're managed values, so
        # they must never surface as widgets.
        for managed in ("BAKE_PADDING", "BAKE_BITS"):
            self.assertIn(f"__{managed}__", bake)
            self.assertNotIn(managed, _params.Parameters.PARAMS)
            self.assertNotIn(managed, used)


class _FakeMesh:
    """A Toolbag ``MeshObject`` stand-in: a name, world bounds, and a parent."""

    def __init__(self, name, lo=(0, 0, 0), hi=(1, 1, 1)):
        self.name = name
        self._bounds = [list(lo), list(hi)]
        self.parent = None

    def getBounds(self):
        return self._bounds


class _FakeContainer:
    """A bake group's High / Low child. Only Low carries the cage offsets."""

    def __init__(self, name, cage=False):
        self.name = name
        if cage:
            self.maxOffset = -1000000.0

    def getChildren(self):
        return []


class _FakeGroup:
    def __init__(self, name):
        self.name = name
        self.source = _FakeContainer("High")
        self.target = _FakeContainer("Low", cage=True)

    def getChildren(self):
        return [self.source, self.target]


class _FakeBaker:
    def __init__(self):
        self.groups = []

    def addGroup(self, name):
        group = _FakeGroup(name)
        self.groups.append(group)
        return group


@unittest.skipUnless(
    _CMDS_IS_MOCKED, "Mock-based test -- run via pytest, not run_tests.py"
)
class TestBakeTemplateGrouping(unittest.TestCase):
    """The bake template's own logic, exercised outside Toolbag.

    ``templates/bake.py`` only ever runs inside toolbag.exe, which is exactly
    why its pure-Python decisions -- which meshes share a bake group, how big
    the cage is -- had no coverage and could strand geometry unnoticed. The
    rendered script is plain Python: executed against a stub ``mset``, those
    functions are directly testable.
    """

    @classmethod
    def setUpClass(cls):
        rendered = MarmosetBridge().render_template(
            template="bake",
            model_path="/tmp/a.fbx",
            manifest_path="/tmp/a.materials.json",
            output_dir="/tmp/out",
            headless=False,
            params={"CAGE_OFFSET": 0.02},
        )
        cls.rendered = rendered

    def _module(self, **overrides):
        """Exec the rendered template into a namespace, with token overrides."""
        stub = MagicMock()
        stub.__name__ = "mset"
        with unittest.mock.patch.dict(sys.modules, {"mset": stub}):
            namespace = {"__name__": "bake_template"}
            exec(compile(self.rendered, "bake.py", "exec"), namespace)  # noqa: S102
        namespace.update(overrides)
        return namespace

    def test_a_source_without_a_name_match_still_reaches_a_target(self):
        """The reported "the doors don't bake onto the room" failure.

        One target name-matches one source mesh and claims its own group; the
        remaining sources used to land in a 'Rest' group with NO target, so
        they projected onto nothing and no cage value could change it.
        """
        mod = self._module()
        room_src, door_a, door_b = (
            _FakeMesh("room"), _FakeMesh("door_a"), _FakeMesh("door_b")
        )
        room_tgt = _FakeMesh("room")
        baker = _FakeBaker()
        mod["_build_groups"](baker, [room_src, door_a, door_b], [room_tgt])

        self.assertEqual(len(baker.groups), 1, "must not isolate and strand")
        group = baker.groups[0]
        self.assertEqual(group.name, "All")
        self.assertIs(room_tgt.parent, group.target)
        for door in (door_a, door_b):
            self.assertIs(door.parent, group.source, f"{door.name} left out of the bake")

    def test_a_target_without_a_source_is_not_left_baking_nothing(self):
        """The mirror case -- an isolated target with no source bakes empty maps."""
        mod = self._module()
        src, tgt, orphan = _FakeMesh("body"), _FakeMesh("body"), _FakeMesh("hatch")
        baker = _FakeBaker()
        mod["_build_groups"](baker, [src], [tgt, orphan])

        self.assertEqual(len(baker.groups), 1)
        self.assertIs(src.parent, baker.groups[0].source)
        self.assertIs(orphan.parent, baker.groups[0].target)

    def test_fully_matched_names_still_isolate_per_pair(self):
        """The isolation is worth keeping when nothing would be stranded."""
        mod = self._module()
        sources = [_FakeMesh("body"), _FakeMesh("hatch")]
        targets = [_FakeMesh("body"), _FakeMesh("hatch")]
        baker = _FakeBaker()
        mod["_build_groups"](baker, sources, targets)

        self.assertEqual(sorted(g.name for g in baker.groups), ["body", "hatch"])
        for source, target in zip(sources, targets):
            self.assertIsNot(source.parent, target.parent)

    def test_leftovers_on_both_sides_pair_in_the_shared_group(self):
        mod = self._module()
        sources = [_FakeMesh("body"), _FakeMesh("bolt")]
        targets = [_FakeMesh("body"), _FakeMesh("nut")]
        baker = _FakeBaker()
        mod["_build_groups"](baker, sources, targets)

        names = sorted(g.name for g in baker.groups)
        self.assertEqual(names, ["Rest", "body"])
        rest = next(g for g in baker.groups if g.name == "Rest")
        self.assertIs(sources[1].parent, rest.source)
        self.assertIs(targets[1].parent, rest.target)

    # -------------------------------------------------------------- cage
    def test_manual_cage_offset_is_used_verbatim(self):
        mod = self._module()
        mod["AUTO_CAGE"] = False
        baker = _FakeBaker()
        mod["_build_groups"](baker, [_FakeMesh("a")], [_FakeMesh("a")])
        self.assertEqual(baker.groups[0].target.maxOffset, 0.02)

    def test_a_manual_offset_too_small_for_the_geometry_is_flagged(self):
        """The OFFICE_ENV failure: a 0.02 cage in a centimetre scene reaches
        nothing that stands off the target, and said nothing about it."""
        import io
        import contextlib

        mod = self._module()
        mod["AUTO_CAGE"] = False
        baker = _FakeBaker()
        target = _FakeMesh("a", (0, 0, 0), (800.0, 400.0, 800.0))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mod["_build_groups"](baker, [_FakeMesh("a")], [target])
        self.assertIn("WARNING", buf.getvalue())
        self.assertIn("will not be baked", buf.getvalue())

    def test_a_sufficient_manual_offset_is_not_flagged(self):
        import io
        import contextlib

        mod = self._module()
        mod["AUTO_CAGE"] = False
        mod["CAGE_OFFSET"] = 50.0
        baker = _FakeBaker()
        target = _FakeMesh("a", (0, 0, 0), (800.0, 400.0, 800.0))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mod["_build_groups"](baker, [_FakeMesh("a")], [target])
        self.assertNotIn("WARNING", buf.getvalue())

    def test_auto_cage_scales_with_the_target_and_clears_the_source(self):
        """The bounds fallback must track scene scale, and cover the gap."""
        mod = self._module()
        mod["AUTO_CAGE"] = True
        for size, gap in ((1.0, 0.0), (100.0, 0.0), (100.0, 3.0)):
            with self.subTest(size=size, gap=gap):
                target = _FakeMesh("a", (0, 0, 0), (size, size, size))
                source = _FakeMesh("a", (-gap, -gap, -gap), (size + gap,) * 3)
                baker = _FakeBaker()
                mod["_build_groups"](baker, [source], [target])
                # The clamps bound the DEPTH the cage reaches, which is half
                # the Toolbag value it is expressed as.
                reach = baker.groups[0].target.maxOffset / mod["CAGE_REACH_FACTOR"]
                diagonal = (3 * size**2) ** 0.5
                self.assertGreaterEqual(reach, diagonal * mod["AUTO_CAGE_BOUNDS_FLOOR"])
                self.assertLessEqual(reach, diagonal * mod["AUTO_CAGE_CEILING"])
                if gap:
                    self.assertGreaterEqual(reach, gap)

    def test_the_bounds_fallback_also_reaches_an_interior_source(self):
        """The fallback is guesswork, but it must not land back on the old value.

        OFFICE_ENV's fixtures need a reach of 8.84 against a 1180.8 diagonal and
        an overhang of only 1.23 -- the case the whole estimator exists for, and
        the one the fallback has to cover when nothing could be measured.
        """
        mod = self._module()
        mod["AUTO_CAGE"] = True
        target = _FakeMesh("room", (0, 0, 0), (681.8, 681.8, 681.8))  # diag ~1180.8
        source = _FakeMesh("room", (-1.23, -1.23, -1.23), (681.8, 681.8, 681.8))
        baker = _FakeBaker()
        mod["_build_groups"](baker, [source], [target])
        self.assertGreater(baker.groups[0].target.maxOffset, 2 * 8.84)

    def test_auto_cage_falls_back_to_the_typed_value_without_bounds(self):
        """Toolbag reports no bounds for some objects; never send a 0 cage."""
        mod = self._module()
        mod["AUTO_CAGE"] = True
        boundless = _FakeMesh("a")
        boundless._bounds = None
        baker = _FakeBaker()
        mod["_build_groups"](baker, [_FakeMesh("a")], [boundless])
        self.assertEqual(baker.groups[0].target.maxOffset, 0.02)

    # ------------------------------------------- host-measured cage standoffs
    def test_the_cage_is_double_the_distance_it_has_to_reach(self):
        """Toolbag's offset spans the ray's full traversal.

        Measured on Toolbag 5.02: a source whose furthest point sits D from the
        target only registers above maxOffset 2*D (D=25 captured at 52 / missed
        50, D=55 at 112 / 110, D=80 at 162 / 160). Halve this factor and the
        bake silently drops the detail, which is exactly the shipped bug.
        """
        mod = self._module()
        mod["AUTO_CAGE"] = True
        mod["CAGE_STANDOFFS"] = {"light": 80.0}
        mod["CAGE_HOST_DIAGONAL"] = 1000.0
        target = _FakeMesh("room", (0, 0, 0), (577.35, 577.35, 577.35))  # diag 1000
        baker = _FakeBaker()
        mod["_build_groups"](baker, [_FakeMesh("light")], [target])

        offset = baker.groups[0].target.maxOffset
        self.assertGreater(offset, 2 * 80.0, "cage cannot reach the source")
        self.assertAlmostEqual(offset, 80.0 * mod["AUTO_CAGE_MARGIN"] * 2.0, places=3)

    def test_a_measured_standoff_beats_the_bounds_estimate(self):
        """The OFFICE_ENV case: a fixture INSIDE the target's box.

        Its overhang is zero, so the bounds path can only fall back to a
        fraction of the target's size -- which was under half what the geometry
        actually needed.
        """
        mod = self._module()
        mod["AUTO_CAGE"] = True
        target = _FakeMesh("room", (0, 0, 0), (681.8, 681.8, 681.8))  # diag ~1180.8
        source = _FakeMesh("light", (100, 100, 100), (200, 200, 200))  # fully inside

        baker = _FakeBaker()
        mod["_build_groups"](baker, [source], [target])
        bounds_only = baker.groups[0].target.maxOffset

        mod["CAGE_STANDOFFS"] = {"light": 8.84}
        mod["CAGE_HOST_DIAGONAL"] = 1180.8
        baker = _FakeBaker()
        mod["_build_groups"](baker, [source], [target])
        measured = baker.groups[0].target.maxOffset

        self.assertGreater(measured, 2 * 8.84, "the light would not be baked")
        self.assertNotAlmostEqual(measured, bounds_only, places=3)

    def test_host_measurements_are_rescaled_to_toolbag_units(self):
        """An import that changes scale must not silently shrink the cage."""
        mod = self._module()
        mod["AUTO_CAGE"] = True
        mod["CAGE_STANDOFFS"] = {"light": 8.0}
        mod["CAGE_HOST_DIAGONAL"] = 1000.0
        # Toolbag reports the same target at a tenth the host's size.
        target = _FakeMesh("room", (0, 0, 0), (57.735, 57.735, 57.735))  # diag 100
        baker = _FakeBaker()
        mod["_build_groups"](baker, [_FakeMesh("light")], [target])

        offset = baker.groups[0].target.maxOffset
        self.assertAlmostEqual(offset, 0.8 * mod["AUTO_CAGE_MARGIN"] * 2.0, places=3)

    def test_sources_absent_from_the_measurements_are_reported(self):
        """A partially-matched table sizes the cage off only what it matched."""
        import contextlib
        import io

        mod = self._module()
        mod["AUTO_CAGE"] = True
        mod["CAGE_STANDOFFS"] = {"light": 8.0}
        mod["CAGE_HOST_DIAGONAL"] = 1000.0
        target = _FakeMesh("room", (0, 0, 0), (577.35, 577.35, 577.35))
        baker = _FakeBaker()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mod["_build_groups"](
                baker, [_FakeMesh("light"), _FakeMesh("vent")], [target]
            )
        self.assertIn("missing from the host's cage measurements", buf.getvalue())
        self.assertIn("vent", buf.getvalue())

    def test_a_measured_standoff_is_warned_about_not_clamped(self):
        """Clipping a measurement back to a 'safe' size is how detail vanishes."""
        import contextlib
        import io

        mod = self._module()
        mod["AUTO_CAGE"] = True
        mod["CAGE_STANDOFFS"] = {"stray": 400.0}
        mod["CAGE_HOST_DIAGONAL"] = 1000.0
        target = _FakeMesh("room", (0, 0, 0), (577.35, 577.35, 577.35))
        baker = _FakeBaker()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mod["_build_groups"](baker, [_FakeMesh("stray")], [target])

        self.assertGreater(baker.groups[0].target.maxOffset, 2 * 400.0)
        self.assertIn("WARNING", buf.getvalue())

    def test_measurements_are_matched_through_fbx_duplicate_suffixes(self):
        """Toolbag's importer appends '.001' to colliding names."""
        mod = self._module()
        mod["AUTO_CAGE"] = True
        mod["CAGE_STANDOFFS"] = {"LIGHT_A": 8.0}
        mod["CAGE_HOST_DIAGONAL"] = 1000.0
        target = _FakeMesh("room", (0, 0, 0), (577.35, 577.35, 577.35))
        baker = _FakeBaker()
        mod["_build_groups"](baker, [_FakeMesh("LIGHT_A.001")], [target])
        self.assertAlmostEqual(
            baker.groups[0].target.maxOffset,
            8.0 * mod["AUTO_CAGE_MARGIN"] * 2.0,
            places=3,
        )


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
            os.path.normpath(ToolbagLog.resolve_toolbag_log_path(exe)),
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
            os.path.normpath(ToolbagLog.resolve_toolbag_log_path(exe)),
            os.path.normpath(expected),
        )

    def test_tier1_works_for_hypothetical_future_version(self):
        """The same code path picks up Toolbag 6 without any source change."""
        expected = self._make_log("6")
        exe = r"D:\custom\Marmoset Toolbag 6\toolbag.exe"
        self.assertEqual(
            os.path.normpath(ToolbagLog.resolve_toolbag_log_path(exe)),
            os.path.normpath(expected),
        )

    def test_tier2_falls_back_to_newest_localappdata_log(self):
        """No version in exe path -> scan LOCALAPPDATA, newest log wins."""
        self._make_log("4", mtime_offset=-3600)  # 1h old
        newer = self._make_log("5", mtime_offset=0)
        exe = r"D:\nonstandard\bin\toolbag.exe"  # no 'Marmoset Toolbag N'
        self.assertEqual(
            os.path.normpath(ToolbagLog.resolve_toolbag_log_path(exe)),
            os.path.normpath(newer),
        )

    def test_returns_none_when_nothing_found(self):
        """No exe, no LOCALAPPDATA Toolbag dirs -> None."""
        self.assertIsNone(ToolbagLog.resolve_toolbag_log_path(None))
        self.assertIsNone(
            ToolbagLog.resolve_toolbag_log_path("/random/path/toolbag.exe")
        )

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
            os.path.normpath(ToolbagLog.resolve_toolbag_log_path(exe)),
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
                    os.path.normpath(ToolbagLog.resolve_toolbag_log_path(exe)),
                    os.path.normpath(expected),
                )


@unittest.skipUnless(
    _CMDS_IS_MOCKED, "Mock-based test -- run via pytest, not run_tests.py"
)
class TestClassifyLogLine(unittest.TestCase):
    """Each Toolbag log line must route to the right severity so the
    bridge panel colour-codes errors visibly (red) and skips (yellow)."""

    def assertLevel(self, expected_level, line):
        result = ToolbagLog.classify_log_line(line)
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
        self.assertLevel("error", "AttributeError: module 'mset' has no attribute 'X'")

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
        self.assertLevel("info", "    + baseColor -> 'Albedo Map' = body_BC.png")

    def test_helper_status_classified_as_info(self):
        self.assertLevel("info", "[toolbag_helpers] Scene contains 2 material(s).")
        self.assertLevel("info", "[Maya->Toolbag] FBX: C:/tmp/x.fbx")

    def test_preload_chatter_is_suppressed(self):
        """Toolbag's shader/image preload spam must not be forwarded."""
        for noise in (
            "opening code data/shader/common/util.sh",
            "opening image data/gui/control/windowbg.tga",
            "opening shader data/shader/post/post.frag",
        ):
            with self.subTest(line=noise):
                self.assertIsNone(ToolbagLog.classify_log_line(noise))

    def test_empty_line_is_suppressed(self):
        self.assertIsNone(ToolbagLog.classify_log_line(""))
        self.assertIsNone(ToolbagLog.classify_log_line("   "))


@unittest.skipUnless(
    _CMDS_IS_MOCKED, "Mock-based test -- run via pytest, not run_tests.py"
)
class TestDispatchLogLines(unittest.TestCase):
    """End-to-end: a sequence of lines drives a real Python logger
    through info/warning/error per the classifier rules."""

    def test_each_level_lands_on_the_matching_logger_call(self):
        logger = unittest.mock.MagicMock()
        lines = [
            "[toolbag_helpers] Scene contains 1 material(s).",  # info
            "  SKIP  'X' -- no matching Toolbag material.",  # warning
            "    ! roughness: MatField not found",  # error
            "opening image data/gui/control/foo.tga",  # suppressed
            "    + baseColor -> 'Albedo Map' = file.png",  # info
        ]
        ToolbagLog.dispatch_log_lines(lines, logger)

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

        thread = ToolbagLog.start_toolbag_log_tail(
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
                return 0  # already exited

        thread = ToolbagLog.start_toolbag_log_tail(
            self.log_path,
            0,
            FakeProcess(),
            unittest.mock.MagicMock(),
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

        thread = ToolbagLog.start_toolbag_log_tail(
            self.log_path,
            0,
            proc,
            logger,
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
        thread = ToolbagLog.start_toolbag_log_tail(
            self.log_path,
            0,
            proc,
            unittest.mock.MagicMock(),
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
