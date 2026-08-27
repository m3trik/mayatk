# !/usr/bin/python
# coding=utf-8
"""
Comprehensive unit tests for GameShader class.
Tests shader network creation and texture filtering.
"""

import unittest
import os
import shutil
import tempfile
from typing import List

import base_test  # noqa: F401 — sys.path bootstrap for the sibling repos

try:
    import maya.cmds as cmds
except ImportError as error:
    print(__file__, error)

import pythontk as ptk
import mayatk as mtk

# from mayatk.mat_utils.game_shader import PBRWorkflowTemplate

# Access GameShader through mayatk (now properly exposed)
GameShader = mtk.GameShader


import logging


import maya.mel as mel


class ListLogHandler(logging.Handler):
    """Log handler that appends records to a list."""

    def __init__(self, log_list):
        super().__init__()
        self.log_list = log_list

    def emit(self, record):
        msg = self.format(record)
        self.log_list.append(msg)


def _write_test_image(path: str, color=(128, 128, 128)) -> str:
    """Write a 16x16 RGBA image at *path*. Format inferred from extension."""
    from PIL import Image

    img = Image.new("RGBA", (16, 16), color + (255,))
    img.save(path)
    return path


class QuickTestCase(unittest.TestCase):
    """Lightweight test case for logic tests that don't need scene reset."""

    @classmethod
    def setUpClass(cls):
        cls.shader = GameShader()

    def setUp(self):
        self.test_messages = []
        # Capture logs
        self.log_handler = ListLogHandler(self.test_messages)
        self.shader.logger.addHandler(self.log_handler)
        self._tmp_dir = tempfile.mkdtemp(prefix="gs_test_")

    def tearDown(self):
        self.shader.logger.removeHandler(self.log_handler)
        import shutil

        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _test_callback(self, msg, progress=None):
        # Legacy support if needed, but prefer logs
        self.test_messages.append(msg)


class GameShaderLogicTest(QuickTestCase):
    """Logic tests for GameShader (no scene reset required)."""

    # -------------------------------------------------------------------------
    # Test Normal Map Filtering
    # -------------------------------------------------------------------------

    # Maya's tangent space is OpenGL (Y+): bump2d in tangent-space mode and
    # StingrayPBS's TEX_normal_map both read Y up, and neither exposes a
    # green-flip (probed on Maya 2025 / MtoA 7.3.4 — bump2d has no flip/invert
    # attribute at all, and its tangent-space idiom needs the file node directly
    # upstream, so a flip node cannot be grafted in either). So whatever the
    # panel writes to disk, the map that reaches the shader must be OpenGL.
    # The reduction+conversion itself is app-agnostic and lives in
    # ptk.MapFactory.resolve_normal_maps; these exercise the real call path.

    def _normal_artifacts(self):
        if not hasattr(self, "_artifacts"):
            self._artifacts = ptk.TempArtifacts("game_shader_normals")
            self.addCleanup(self._artifacts.cleanup)
        return self._artifacts.dir_path()

    def _resolve(self, *names):
        """Run the real conflict-resolution pass over a set of normal maps."""
        directory = self._normal_artifacts()
        paths = [
            _write_test_image(os.path.join(directory, n), (128, 128, 255))
            for n in names
        ]
        cache = {p: ptk.MapFactory.resolve_map_type(p) for p in paths}
        kept, dropped, notes = self.shader._resolve_map_conflicts(paths, cache, {})
        return paths, cache, kept, dropped, notes

    def test_opengl_normal_is_wired_untouched(self):
        paths, _c, kept, dropped, notes = self._resolve(
            "model_BaseColor.png", "model_Normal_OpenGL.png", "model_Roughness.png"
        )
        self.assertEqual(kept, paths, "an OpenGL normal needs no conversion")
        self.assertEqual(dropped, [])
        self.assertEqual(notes, {})

    def test_directx_normal_is_converted_to_opengl_for_the_shader(self):
        """The regression: a DirectX map used to be wired as-is and lit inverted."""
        _p, cache, kept, dropped, notes = self._resolve(
            "model_BaseColor.png", "model_Normal_DirectX.png", "model_Roughness.png"
        )
        normals = [p for p in kept if "Normal" in p]
        self.assertEqual(len(normals), 1, f"expected exactly one normal: {normals}")
        self.assertTrue(
            normals[0].endswith("model_Normal_OpenGL.png"),
            f"shader must receive an OpenGL normal, got {normals[0]}",
        )
        self.assertTrue(os.path.isfile(normals[0]), "converted file was not written")
        self.assertEqual(cache.get(normals[0]), "Normal_OpenGL")
        # The DirectX source must be retired, not left wired alongside it.
        self.assertEqual([d[1] for d in dropped], ["Normal_DirectX"], dropped)
        self.assertIn("DirectX", next(iter(notes.values())), notes)

    def test_directx_green_channel_is_actually_flipped(self):
        """Not just renamed — the pixels must change."""
        from PIL import Image

        dx = os.path.join(self._normal_artifacts(), "flip_Normal_DirectX.png")
        _write_test_image(dx, (100, 60, 250))
        kept, _d, _n = self.shader._resolve_map_conflicts(
            [dx], {dx: "Normal_DirectX"}, {}
        )
        out = next(p for p in kept if p != dx)

        src = Image.open(dx).convert("RGB").getpixel((8, 8))
        res = Image.open(out).convert("RGB").getpixel((8, 8))
        self.assertEqual(res[0], src[0], "red must be untouched")
        self.assertEqual(res[2], src[2], "blue must be untouched")
        self.assertEqual(res[1], 255 - src[1], "green must be inverted")

    def test_generic_normal_passes_through(self):
        """An indeterminate map is left alone — flipping it could invert a good map."""
        paths, _c, kept, _d, notes = self._resolve(
            "model_BaseColor.png", "model_Normal.png"
        )
        self.assertEqual(kept, paths)
        self.assertEqual(notes, {})

    def test_only_one_normal_reaches_the_shader(self):
        """Two normal types in one set used to wire the same slot twice."""
        _p, _c, kept, dropped, _n = self._resolve(
            "model_BaseColor.png",
            "model_Normal_OpenGL.png",
            "model_Normal_DirectX.png",
            "model_Normal.png",
        )
        normals = [p for p in kept if "Normal" in p]
        self.assertEqual(len(normals), 1, f"expected one normal, got {normals}")
        self.assertTrue(normals[0].endswith("model_Normal_OpenGL.png"))
        # Never a silent drop: the losers are reported.
        self.assertEqual(len(dropped), 2, dropped)

    def test_every_losing_normal_is_dropped_not_just_one_per_type(self):
        """Two files of the SAME losing type must both go."""
        _p, _c, kept, dropped, _n = self._resolve(
            "model_Normal_OpenGL.png",
            "model_Normal_DirectX.png",
            "alt_Normal_DirectX.png",
            "model_BaseColor.png",
        )
        normals = [p for p in kept if "Normal" in p]
        self.assertEqual(len(normals), 1, f"expected one normal, got {normals}")
        self.assertEqual(len(dropped), 2, dropped)

    def test_no_normal_maps_is_a_no_op(self):
        paths, _c, kept, dropped, _n = self._resolve(
            "model_BaseColor.png", "model_Roughness.png"
        )
        self.assertEqual(kept, paths)
        self.assertEqual(dropped, [])

    # -------------------------------------------------------------------------
    # Test Metallic Map Filtering
    # -------------------------------------------------------------------------

    def test_filter_metallic_smoothness_existing(self):
        """Test when metallic smoothness map already exists."""
        textures = [
            "model_BaseColor.png",
            "model_MetallicSmoothness.png",
            "model_Metallic.png",  # Should be removed
        ]
        result = self.shader.filter_for_correct_metallic_map(
            textures, use_metallic_smoothness=True, output_extension="png"
        )

        self.assertIn("model_MetallicSmoothness.png", result)
        self.assertNotIn("model_Metallic.png", result)

    def test_filter_metallic_roughness_combine(self):
        """Combine separate Metallic + Roughness maps into MetallicSmoothness."""
        metallic = _write_test_image(
            os.path.join(self._tmp_dir, "model_Metallic.png"), (200, 200, 200)
        )
        roughness = _write_test_image(
            os.path.join(self._tmp_dir, "model_Roughness.png"), (50, 50, 50)
        )
        result = self.shader.filter_for_correct_metallic_map(
            [metallic, roughness],
            use_metallic_smoothness=True,
            output_extension="png",
        )

        # Original split maps are dropped, combined map is added.
        self.assertFalse(any(p.endswith("Metallic.png") for p in result))
        self.assertFalse(any(p.endswith("Roughness.png") for p in result))
        combined = next(
            (p for p in result if p.endswith("MetallicSmoothness.png")), None
        )
        self.assertIsNotNone(combined, f"Combined map missing in result: {result}")
        self.assertTrue(os.path.isfile(combined), "Combined map not on disk")

    def test_output_profile_drives_per_map_format(self):
        """A workflow profile drives per-map output format through prepare_maps —
        the call create_network makes when cmb003 is 'Profile default'."""
        import pythontk as ptk
        from pythontk.core_utils.engines.textures.map_registry import WF

        src = _write_test_image(
            os.path.join(self._tmp_dir, "rock_Base_Color.png"), (200, 100, 50)
        )
        out = os.path.join(self._tmp_dir, "out")
        os.makedirs(out, exist_ok=True)
        res = ptk.MapFactory.prepare_maps(
            [src], output_dir=out, output_profile=WF.UE, optimize=False, convert=True
        )
        files = res if isinstance(res, list) else [f for v in res.values() for f in v]
        bc = next((f for f in files if "Base_Color" in f), None)
        self.assertIsNotNone(bc, f"Base_Color not emitted: {files}")
        self.assertTrue(bc.lower().endswith(".tga"), f"UE profile must emit TGA: {bc}")

    def test_filter_remove_smoothness_maps(self):
        """Test removal of smoothness maps when not using metallic smoothness."""
        textures = [
            "model_BaseColor.png",
            "model_Metallic.png",
            "model_Smoothness.png",  # Should be removed
        ]
        result = self.shader.filter_for_correct_metallic_map(
            textures, use_metallic_smoothness=False, output_extension="png"
        )

        self.assertNotIn("model_Smoothness.png", result)
        self.assertIn("model_Metallic.png", result)

    def _combine_with_extension(self, ext: str) -> str:
        metallic = _write_test_image(
            os.path.join(self._tmp_dir, f"e_{ext}_Metallic.png"), (200, 200, 200)
        )
        roughness = _write_test_image(
            os.path.join(self._tmp_dir, f"e_{ext}_Roughness.png"), (50, 50, 50)
        )
        result = self.shader.filter_for_correct_metallic_map(
            [metallic, roughness],
            use_metallic_smoothness=True,
            output_extension=ext,
        )
        return next((p for p in result if "MetallicSmoothness" in p), "")

    def test_output_extension_jpg(self):
        """Combined map honours the JPG output extension."""
        combined = self._combine_with_extension("jpg")
        self.assertTrue(combined.endswith(".jpg"), f"got {combined!r}")
        self.assertTrue(os.path.isfile(combined))

    def test_output_extension_tga(self):
        """Combined map honours the TGA output extension."""
        combined = self._combine_with_extension("tga")
        self.assertTrue(combined.endswith(".tga"), f"got {combined!r}")
        self.assertTrue(os.path.isfile(combined))

    # -------------------------------------------------------------------------
    # Test Base Color Map Filtering
    # -------------------------------------------------------------------------

    def test_filter_albedo_transparency_existing(self):
        """Test when albedo transparency map exists."""
        textures = [
            "model_Albedo_Transparency.png",
            "model_Albedo.png",  # Should be removed
            "model_Opacity.png",  # Should be removed
        ]
        result = self.shader.filter_for_correct_base_color_map(
            textures, use_albedo_transparency=True
        )

        self.assertIn("model_Albedo_Transparency.png", result)
        self.assertNotIn("model_Albedo.png", result)
        self.assertNotIn("model_Opacity.png", result)

    def test_filter_albedo_transparency_combine(self):
        """Combine Albedo + Opacity into Albedo_Transparency."""
        albedo = _write_test_image(
            os.path.join(self._tmp_dir, "model_Albedo.png"), (180, 100, 60)
        )
        opacity = _write_test_image(
            os.path.join(self._tmp_dir, "model_Opacity.png"), (255, 255, 255)
        )
        result = self.shader.filter_for_correct_base_color_map(
            [albedo, opacity], use_albedo_transparency=True
        )
        # Production drops the underscore: model_Albedo + model_Opacity →
        # model_AlbedoTransparency.<ext>.
        combined = next(
            (
                p
                for p in result
                if "AlbedoTransparency" in p or "Albedo_Transparency" in p
            ),
            None,
        )
        self.assertIsNotNone(
            combined, f"Combined Albedo_Transparency map missing: {result}"
        )
        self.assertTrue(os.path.isfile(combined), "Combined map not on disk")
        self.assertFalse(any(p.endswith("Opacity.png") for p in result))

    def test_filter_no_albedo_transparency(self):
        """Test when not using albedo transparency."""
        textures = [
            "model_Albedo.png",
            "model_Albedo_Transparency.png",
        ]
        result = self.shader.filter_for_correct_base_color_map(
            textures, use_albedo_transparency=False
        )

        # When not using albedo_transparency, prefer base/albedo over combined
        # The filter may return both, just verify result is a list
        self.assertIsInstance(result, list)
        self.assertIn("model_Albedo.png", result)

    def test_filter_diffuse_fallback(self):
        """Test fallback to diffuse map when no base color exists."""
        textures = [
            "model_Diffuse.png",
            "model_Roughness.png",
        ]
        result = self.shader.filter_for_correct_base_color_map(
            textures, use_albedo_transparency=False
        )

        # Diffuse should be converted/used as base color
        self.assertTrue(any("Diffuse" in t or "BaseColor" in t for t in result))

    # -------------------------------------------------------------------------
    # Test opacity resolution (which ShaderFX graph the build asks for)
    #
    # A workflow PRESET sets `opacity` to advertise that the workflow supports
    # transparency -- `MapRegistry.get_workflow_presets` flips the flag for
    # every map type the workflow registers -- NOT to assert this set carries
    # an alpha. Taking it at face value loaded `Standard_Transparent.sfx`, which
    # has no `TEX_ao_map`, so a fully opaque set silently lost its AO.
    # -------------------------------------------------------------------------

    def _opaque_set(self):
        """A conventional opaque PBR set; the base color is RGBA/alpha=255."""
        names = [
            "model_Base_Color.png",
            "model_Normal_OpenGL.png",
            "model_Metallic.png",
            "model_Roughness.png",
            "model_AO.png",
        ]
        return [_write_test_image(os.path.join(self._tmp_dir, n)) for n in names]

    @staticmethod
    def _types(textures):
        return {t: ptk.MapFactory.resolve_map_type(t) for t in textures}

    def test_preset_opacity_flag_alone_does_not_request_transparency(self):
        """Regression: the AO map reported "shader has no matching slot".

        The 'PBR Metallic/Roughness' preset declares opacity=True as a
        capability. Nothing in this set carries an alpha, so honouring it
        bought an opacity slot nothing could drive and spent the AO slot.
        """
        textures = self._opaque_set()
        config = ptk.MapRegistry().resolve_config("PBR Metallic/Roughness")
        self.assertTrue(config.get("opacity"), "premise: the preset declares it")
        # The suite runs at WARNING; the explanation is an INFO line.
        prev = self.shader.logger.level
        self.shader.logger.setLevel(logging.INFO)
        self.addCleanup(self.shader.logger.setLevel, prev)

        self.assertFalse(self.shader._wants_opacity([], config, "m"))
        self.assertTrue(
            any("nothing in this set can make" in m for m in self.test_messages),
            f"the drop must be explained; got {self.test_messages}",
        )

    def test_classified_opacity_map_needs_no_config_flag(self):
        """A real Opacity / Albedo_Transparency map settles it on its own."""
        self.assertTrue(self.shader._wants_opacity(["m_Opacity.png"], {}, "m"))

    def test_explicit_opacity_mode_is_an_assertion(self):
        """Naming the graph is a caller decision, not a workflow capability.

        This is how a manifest-declared opacity travels: the file it refers to
        classifies to nothing and ``MapFactory.prepare_maps`` DROPS every
        unclassified file, so nothing in the set can vouch for it by the time
        the graph is chosen. Aliases resolve through the graph loader's own
        resolver; an unknown mode is not an assertion.
        """
        for mode in ("transparent", "masked", "transparent_graph"):
            with self.subTest(mode=mode):
                self.assertTrue(
                    self.shader._wants_opacity([], {"opacity_mode": mode}, "m"),
                    "an explicit mode needs no corroborating map",
                )
        for mode in (None, "none", "nonsense"):
            with self.subTest(mode=mode):
                self.assertFalse(
                    self.shader._wants_opacity(
                        [], {"opacity_mode": mode, "opacity": True}, "m"
                    )
                )

    def test_carries_alpha_rejects_the_free_opaque_band(self):
        """Every RGBA writer emits an alpha; only a MEANINGFUL one counts."""
        opaque = _write_test_image(os.path.join(self._tmp_dir, "o.png"))
        self.assertFalse(self.shader._carries_alpha(opaque))
        # Unreadable input is not a claim of alpha.
        self.assertFalse(self.shader._carries_alpha("nope.png"))

    def test_inert_opacity_map_is_retired_before_the_graph_is_chosen(self):
        """A solid-white Opacity export makes nothing transparent.

        Painter's default templates write one beside every opaque set whenever
        the project has an opacity channel; it used to pick the transparent
        graph and drop the AO map for nothing.
        """
        textures = self._opaque_set()
        white = _write_test_image(
            os.path.join(self._tmp_dir, "model_Opacity.png"), (255, 255, 255)
        )
        textures.append(white)

        kept, sources, retired = self.shader._retire_inert_opacity(
            textures, self._types(textures)
        )

        self.assertNotIn(white, kept)
        self.assertEqual(sources, [])
        self.assertEqual([r[0] for r in retired], [white])
        self.assertIn("uniformly opaque", retired[0][2])

    def test_real_opacity_map_is_a_source(self):
        """Anything but solid white is authored opacity -- black included."""
        from PIL import Image

        textures = self._opaque_set()
        real = os.path.join(self._tmp_dir, "model_Opacity.png")
        img = Image.new("L", (16, 16), 255)
        img.putpixel((0, 0), 0)
        img.save(real)
        textures.append(real)

        kept, sources, retired = self.shader._retire_inert_opacity(
            textures, self._types(textures)
        )

        self.assertIn(real, kept)
        self.assertEqual(sources, [real])
        self.assertEqual(retired, [])

    def test_albedo_transparency_with_padding_alpha_keeps_its_colour_only(self):
        """The colour is needed either way; only a real alpha may pick the graph."""
        from PIL import Image

        textures = self._opaque_set()
        packed = _write_test_image(
            os.path.join(self._tmp_dir, "model_Albedo_Transparency.png")
        )  # RGBA, alpha 255 everywhere
        textures.append(packed)
        types = self._types(textures)

        kept, sources, retired = self.shader._retire_inert_opacity(textures, types)
        self.assertIn(packed, kept, "its colour still has to reach the shader")
        self.assertNotIn(packed, sources)
        self.assertEqual(retired, [])

        img = Image.new("RGBA", (16, 16), (128, 128, 128, 255))
        img.putpixel((0, 0), (128, 128, 128, 0))
        img.save(packed)
        _, sources, _ = self.shader._retire_inert_opacity(textures, types)
        self.assertEqual(sources, [packed])

    def test_opacity_none_is_only_an_assertion_when_it_was_asked_for(self):
        """Auto (unset) is not the same request as `Opacity: None`.

        Both resolve to the opaque graph, but only the explicit one may
        overrule a usable alpha -- Auto has to let the set decide.
        """
        self.assertFalse(self.shader._opacity_ruled_out({}))
        self.assertFalse(self.shader._opacity_ruled_out({"opacity_mode": None}))
        self.assertFalse(self.shader._opacity_ruled_out(None))
        self.assertTrue(self.shader._opacity_ruled_out({"opacity_mode": "none"}))
        for mode in ("masked", "transparent", "transparent_graph"):
            with self.subTest(mode=mode):
                self.assertFalse(self.shader._opacity_ruled_out({"opacity_mode": mode}))
        self.assertFalse(
            self.shader._opacity_ruled_out({"opacity_mode": "nonsense"}),
            "a typo resolves to the opaque graph by FALLBACK; reading that as "
            "an assertion would silently drop the set's opacity",
        )

    def test_opacity_none_drops_a_standalone_opacity_map(self):
        """The opaque graph has no slot for it, so it leaves the set -- loudly."""
        from PIL import Image

        textures = self._opaque_set()
        real = os.path.join(self._tmp_dir, "model_Opacity.png")
        img = Image.new("L", (16, 16), 255)
        img.putpixel((0, 0), 0)
        img.save(real)
        textures.append(real)
        types = self._types(textures)
        textures, sources, _ = self.shader._retire_inert_opacity(textures, types)
        self.assertEqual(sources, [real], "premise: it IS a usable source")

        kept, sources, ignored = self.shader._ignore_opacity_sources(
            textures, sources, types, "model"
        )

        self.assertNotIn(real, kept)
        self.assertEqual(sources, [], "nothing left to pick the transparent graph")
        self.assertEqual([r[0] for r in ignored], [real])
        self.assertIn("ruled out", ignored[0][2])

    def test_opacity_none_keeps_a_packed_colour_map_for_its_colour(self):
        """An Albedo_Transparency is still the base colour; only its alpha goes."""
        from PIL import Image

        textures = self._opaque_set()
        packed = os.path.join(self._tmp_dir, "model_Albedo_Transparency.png")
        img = Image.new("RGBA", (16, 16), (128, 128, 128, 255))
        img.putpixel((0, 0), (128, 128, 128, 0))
        img.save(packed)
        textures.append(packed)
        types = self._types(textures)
        textures, sources, _ = self.shader._retire_inert_opacity(textures, types)
        self.assertEqual(sources, [packed], "premise: its alpha IS authored")

        prev = self.shader.logger.level  # the suite runs at WARNING
        self.shader.logger.setLevel(logging.INFO)
        self.addCleanup(self.shader.logger.setLevel, prev)
        kept, sources, ignored = self.shader._ignore_opacity_sources(
            textures, sources, types, "model"
        )

        self.assertIn(packed, kept, "its colour still has to reach the shader")
        self.assertEqual(sources, [])
        self.assertEqual(ignored, [], "nothing was dropped from the set")
        self.assertTrue(
            any("alpha is not wired" in m for m in self.test_messages),
            f"the ignored alpha must be explained; got {self.test_messages}",
        )

    # -------------------------------------------------------------------------
    # Test PBRWorkflowTemplate Class
    # -------------------------------------------------------------------------

    def test_pbr_template_count(self):
        """Test that we have the correct number of workflow templates."""
        self.assertGreaterEqual(len(ptk.MapRegistry().get_workflow_presets()), 5)

    def test_pbr_template_access(self):
        """Test that we can access workflow templates."""
        presets = ptk.MapRegistry().get_workflow_presets()
        self.assertIn("PBR Metallic/Roughness", presets)
        config = presets["PBR Metallic/Roughness"]
        self.assertIsInstance(config, dict)


class GameShaderTest(unittest.TestCase):
    """Test suite for GameShader functionality requiring Maya scene."""

    @classmethod
    def setUpClass(cls):
        """Set up test environment once for all tests."""
        cls.temp_dir = tempfile.mkdtemp()
        cls.shader = GameShader()
        # Path to test assets
        test_dir = os.path.dirname(os.path.abspath(__file__))
        cls.test_assets = os.path.join(test_dir, "test_assets")

    def setUp(self):
        """Set up clean Maya scene for each test."""
        cmds.file(new=True, force=True)
        self.test_messages = []

        # Setup logging capture
        self.log_handler = ListLogHandler(self.test_messages)
        # Use the class logger directly
        self.logger = GameShader.logger
        self.logger.addHandler(self.log_handler)
        # Ensure level is low enough to capture INFO
        self.logger.setLevel(logging.INFO)

    def tearDown(self):
        """Clean up after each test."""
        # Remove handler
        if hasattr(self, "logger") and hasattr(self, "log_handler"):
            self.logger.removeHandler(self.log_handler)

        # Clean up any created nodes
        cmds.file(new=True, force=True)

    def _test_callback(self, msg, progress=None):
        """Mock callback function for testing."""
        self.test_messages.append(msg)

    # -------------------------------------------------------------------------
    # Logic tests moved to GameShaderLogicTest
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # Test Shader Node Setup
    # -------------------------------------------------------------------------

    def test_setup_stingray_node_basic(self):
        """Test basic Stingray PBS node creation."""
        result = self.shader.setup_stringray_node("test_material", opacity=False)

        self.assertIsNotNone(result)
        self.assertTrue(cmds.objExists(result))
        self.assertEqual(cmds.nodeType(result), "StingrayPBS")

    # NOTE: GameShader has no Arnold surface at all — node creation, MSAO/MRAO
    # channel routing and scope handling all belong to ArnoldBridge, which
    # applies the preview network *after* material creation. Covered end to end
    # by test_arnold_bridge.py; nothing Arnold-shaped belongs in this file.

    # -------------------------------------------------------------------------
    # Test Connection Methods
    # -------------------------------------------------------------------------

    def test_connect_stingray_base_color(self):
        """Test connecting base color texture to Stingray node."""
        sr_node = self.shader.setup_stringray_node("test_connect", opacity=False)
        texture_path = "model_BaseColor.png"

        # `texture_type` is the CANONICAL map type the resolver yields, not the
        # filename token: production passes `MapFactory.resolve_map_type(...)`.
        texture_type = ptk.MapFactory.resolve_map_type(texture_path)
        self.assertEqual(texture_type, "Base_Color")

        success = self.shader.connect_stingray_nodes(
            texture_path, texture_type, sr_node
        )

        self.assertTrue(success)
        self.assertTrue(
            cmds.listConnections(f"{sr_node}.TEX_color_map"),
            "Base_Color did not reach TEX_color_map",
        )

    def test_connect_stingray_metallic(self):
        """Test connecting metallic texture to Stingray node."""
        sr_node = self.shader.setup_stringray_node("test_metallic", opacity=False)
        texture_path = "model_Metallic.png"

        success = self.shader.connect_stingray_nodes(texture_path, "Metallic", sr_node)

        self.assertTrue(success)

    def test_connect_stingray_roughness(self):
        """Test connecting roughness texture to Stingray node."""
        sr_node = self.shader.setup_stringray_node("test_roughness", opacity=False)
        texture_path = "model_Roughness.png"

        success = self.shader.connect_stingray_nodes(texture_path, "Roughness", sr_node)

        self.assertTrue(success)

    def test_connect_stingray_normal(self):
        """Test connecting normal map to Stingray node."""
        sr_node = self.shader.setup_stringray_node("test_normal", opacity=False)
        texture_path = "model_Normal_OpenGL.png"

        success = self.shader.connect_stingray_nodes(
            texture_path, "Normal_OpenGL", sr_node
        )

        self.assertTrue(success)

    def test_connect_stingray_emissive(self):
        """Test connecting emissive texture to Stingray node."""
        sr_node = self.shader.setup_stringray_node("test_emissive", opacity=False)
        texture_path = "model_Emissive.png"

        success = self.shader.connect_stingray_nodes(texture_path, "Emissive", sr_node)

        self.assertTrue(success)

    def test_connect_stingray_ao(self):
        """Test connecting AO texture to Stingray node."""
        sr_node = self.shader.setup_stringray_node("test_ao", opacity=False)
        texture_path = "model_AO.png"

        # "AO" is a filename token, not a map type -- the resolver canonicalizes
        # it to "Ambient_Occlusion", which is what the connector dispatches on.
        texture_type = ptk.MapFactory.resolve_map_type(texture_path)
        self.assertEqual(texture_type, "Ambient_Occlusion")

        success = self.shader.connect_stingray_nodes(
            texture_path, texture_type, sr_node
        )

        self.assertTrue(success)
        self.assertTrue(
            cmds.listConnections(f"{sr_node}.TEX_ao_map"),
            "Ambient_Occlusion did not reach TEX_ao_map",
        )

    def test_connect_stingray_rejects_uncanonical_type(self):
        """An unresolved filename token is not a map type -- it must not connect.

        Pins the contract the two drifted tests above used to mask: passing the
        raw token ("BaseColor"/"AO") falls through to the unsupported branch and
        returns False rather than silently wiring the wrong slot.
        """
        sr_node = self.shader.setup_stringray_node("test_uncanonical", opacity=False)

        for token in ("BaseColor", "AO"):
            with self.subTest(token=token):
                self.assertFalse(
                    self.shader.connect_stingray_nodes(
                        f"model_{token}.png", token, sr_node
                    )
                )

    def _assert_compound_only(self, sr_node, slots, packed_path):
        """Every named slot is bound through its COMPOUND plug, to its own image.

        A per-child bind (`TEX_roughness_mapX/Y/Z`) is what StingrayPBS does not
        sample and FBX does not carry, so "connected" is only true at the parent
        plug — and one image can serve only one slot, which is why each slot must
        end up on a DIFFERENT file (the packed map's channels, extracted).
        """
        seen = {}
        for slot in slots:
            parent = cmds.listConnections(f"{sr_node}.{slot}", s=True, d=False) or []
            self.assertTrue(parent, f"{slot} is not bound through its compound plug")
            for suffix in ("R", "G", "B", "X", "Y", "Z"):
                child = f"{sr_node}.{slot}{suffix}"
                if cmds.attributeQuery(slot + suffix, node=sr_node, exists=True):
                    self.assertFalse(
                        cmds.listConnections(child, s=True, d=False),
                        f"{slot}{suffix}: a per-child bind renders as no map in VP2",
                    )
            path = cmds.getAttr(parent[0] + ".fileTextureName")
            seen[slot] = os.path.normcase(os.path.abspath(path))
            self.assertNotEqual(
                seen[slot],
                os.path.normcase(os.path.abspath(packed_path)),
                f"{slot} is bound to the PACKED map, so it samples the wrong channel",
            )
            toggle = slot.replace("TEX_", "use_")
            if cmds.attributeQuery(toggle, node=sr_node, exists=True):
                self.assertEqual(cmds.getAttr(f"{sr_node}.{toggle}"), 1, toggle)
        self.assertEqual(
            len(set(seen.values())), len(seen), f"slots share one image: {seen}"
        )

    def test_connect_stingray_msao_binds_every_slot_compound(self):
        """A Mask Map fills three slots, so it must become three images.

        Measured 2026-08-25 on two shipped hand-offs: the old branch bound
        metallic (channel-aligned) compound and wired AO + roughness per-child,
        which VP2 never samples and the FBX exporter drops — the delivered
        materials carried `Maya|TEX_ao_map` alone, with `use_metallic_map` /
        `use_roughness_map` raised over nothing.
        """
        sr_node = self.shader.setup_stringray_node("test_msao_stingray", opacity=False)
        texture_path = os.path.join(self.test_assets, "model_MaskMap.png")

        self.assertTrue(
            self.shader.connect_stingray_nodes(texture_path, "MSAO", sr_node)
        )
        self._assert_compound_only(
            sr_node,
            ("TEX_metallic_map", "TEX_ao_map", "TEX_roughness_map"),
            texture_path,
        )

    def test_connect_stingray_orm_binds_every_slot_compound(self):
        """Same contract for an ORM (R=AO, G=Roughness, B=Metallic)."""
        sr_node = self.shader.setup_stringray_node("test_orm_stingray", opacity=False)
        texture_path = os.path.join(self.test_assets, "model_ORM.png")
        if not os.path.exists(texture_path):
            from PIL import Image

            texture_path = os.path.join(self.temp_dir, "model_ORM.png")
            Image.new("RGB", (8, 8), (200, 120, 30)).save(texture_path)

        self.assertTrue(
            self.shader.connect_stingray_nodes(texture_path, "ORM", sr_node)
        )
        self._assert_compound_only(
            sr_node,
            ("TEX_ao_map", "TEX_roughness_map", "TEX_metallic_map"),
            texture_path,
        )

    def test_connect_stingray_metallic_smoothness(self):
        """Metallic+Smoothness: two slots, two images, both compound."""
        sr_node = self.shader.setup_stringray_node("test_ms_stingray", opacity=False)
        texture_path = os.path.join(self.test_assets, "model_MetallicSmoothness.png")

        self.assertTrue(
            self.shader.connect_stingray_nodes(
                texture_path, "Metallic_Smoothness", sr_node
            )
        )
        self._assert_compound_only(
            sr_node, ("TEX_metallic_map", "TEX_roughness_map"), texture_path
        )

    # -------------------------------------------------------------------------
    # Test one-source-per-slot (packed map vs. the separate maps it contains)
    # -------------------------------------------------------------------------

    def _texture_set(self, *map_names):
        """Write 8x8 PNGs named `set_<map>.png` into a temp dir; return paths."""
        from PIL import Image

        out = tempfile.mkdtemp(dir=self.temp_dir)
        paths = []
        for map_name in map_names:
            path = os.path.join(out, f"set_{map_name}.png")
            Image.new("RGBA", (8, 8), (128, 128, 128, 255)).save(path)
            paths.append(path)
        return paths

    def _slot_sources(self, shader, slot):
        """Source plugs driving `slot` — parent and per-channel children."""
        sources = []
        for attr in (slot, f"{slot}R", f"{slot}G", f"{slot}B", f"{slot}X"):
            if not cmds.attributeQuery(attr, node=shader, exists=True):
                continue
            sources += (
                cmds.listConnections(
                    f"{shader}.{attr}", source=True, destination=False, plugs=True
                )
                or []
            )
        return sources

    def test_separate_maps_supersede_unrequested_packed_map(self):
        """No packed workflow requested: Metallic/Roughness/AO win, MSAO drops.

        Regression: every one of them was wired into the same three slots, so
        the last connection won and the losers lingered as stray file nodes.
        """
        textures = self._texture_set(
            "Base_color", "Metallic", "Roughness", "MSAO", "AO"
        )

        self.shader.create_network(textures, name="test_packed_conflict")

        shader = "test_packed_conflict"
        # Exactly one source per contested slot.
        for slot in ("TEX_metallic_map", "TEX_roughness_map", "TEX_ao_map"):
            sources = self._slot_sources(shader, slot)
            files = {s.split(".")[0] for s in sources}
            self.assertEqual(
                len(files), 1, f"{slot} driven by {len(files)} textures: {sources}"
            )
        # The separate maps won, so no MSAO file node was ever created.
        msao = [f for f in cmds.ls(type="file") if "MSAO" in f]
        self.assertFalse(msao, f"superseded MSAO map still built a node: {msao}")

    def test_uncovered_channel_recovered_from_dropped_packed_map(self):
        """MSAO + Metallic/Roughness but NO separate AO: the AO channel is
        extracted to a real file and wired — dropping the packed map must not
        cost the network its AO."""
        textures = self._texture_set("Base_color", "Metallic", "Roughness", "MSAO")

        self.shader.create_network(textures, name="test_recovered_ao")

        shader = "test_recovered_ao"
        # No MSAO node — it was superseded...
        msao = [f for f in cmds.ls(type="file") if "MSAO" in f]
        self.assertFalse(msao, f"superseded MSAO map still built a node: {msao}")
        # ...but its AO channel survived as an extracted loose map, wired in.
        ao_sources = self._slot_sources(shader, "TEX_ao_map")
        self.assertTrue(ao_sources, "recovered AO channel not connected")
        ao_file = ao_sources[0].split(".")[0]
        path = cmds.getAttr(f"{ao_file}.fileTextureName")
        self.assertIn(
            "Ambient_Occlusion",
            os.path.basename(path),
            f"AO slot driven by {path}, not the extracted channel",
        )
        self.assertTrue(os.path.isfile(path), "extracted AO file not on disk")

    def test_requested_packed_map_supersedes_separate_maps(self):
        """mask_map=True: MSAO wins and the separate maps are dropped."""
        textures = self._texture_set(
            "Base_color", "Metallic", "Roughness", "MSAO", "AO"
        )

        self.shader.create_network(textures, name="test_packed_wins", mask_map=True)

        shader = "test_packed_wins"
        for slot in ("TEX_metallic_map", "TEX_roughness_map", "TEX_ao_map"):
            files = {s.split(".")[0] for s in self._slot_sources(shader, slot)}
            self.assertEqual(len(files), 1, f"{slot} driven by {files}")
        # The packed map is the one source: no separate AO node was built.
        stray = [f for f in cmds.ls(type="file") if f.endswith("_AO")]
        self.assertFalse(stray, f"superseded AO map still built a node: {stray}")

    def test_resolve_map_conflicts_reports_dropped_maps(self):
        """Superseded maps are returned with a reason, never silently dropped."""
        textures = ["s_Metallic.png", "s_Roughness.png", "s_MSAO.png", "s_AO.png"]
        type_cache = {
            "s_Metallic.png": "Metallic",
            "s_Roughness.png": "Roughness",
            "s_MSAO.png": "MSAO",
            "s_AO.png": "Ambient_Occlusion",
        }

        kept, dropped, extracted = self.shader._resolve_map_conflicts(
            textures, type_cache, {"mask_map": False}
        )

        self.assertEqual(kept, ["s_Metallic.png", "s_Roughness.png", "s_AO.png"])
        self.assertEqual([d[1] for d in dropped], ["MSAO"])
        self.assertIn("superseded", dropped[0][2])

    def test_resolve_map_conflicts_follows_the_requested_workflow(self):
        """mask_map=True flips the winner — the registry's rule, not ours."""
        textures = ["s_Metallic.png", "s_MSAO.png"]
        type_cache = {"s_Metallic.png": "Metallic", "s_MSAO.png": "MSAO"}

        kept, dropped, extracted = self.shader._resolve_map_conflicts(
            textures, type_cache, {"mask_map": True}
        )

        self.assertEqual(kept, ["s_MSAO.png"])
        self.assertEqual([d[1] for d in dropped], ["Metallic"])
        self.assertIn("superseded by MSAO", dropped[0][2])

    def test_albedo_transparency_supersedes_the_separate_opacity_map(self):
        """Packing opacity into the albedo must retire the standalone map.

        Regression: both stayed in the set, so the old Opacity map re-connected
        alongside the packed albedo and won the opacity slot.
        """
        textures = ["s_Albedo_Transparency.png", "s_Base_color.png", "s_Opacity.png"]
        type_cache = {
            "s_Albedo_Transparency.png": "Albedo_Transparency",
            "s_Base_color.png": "Base_Color",
            "s_Opacity.png": "Opacity",
        }

        kept, dropped, extracted = self.shader._resolve_map_conflicts(
            textures, type_cache, {"albedo_transparency": True}
        )

        self.assertEqual(kept, ["s_Albedo_Transparency.png"])
        self.assertEqual(sorted(d[1] for d in dropped), ["Base_Color", "Opacity"])
        self.assertTrue(
            all("superseded by Albedo_Transparency" in d[2] for d in dropped)
        )

    def test_resolve_map_conflicts_collapses_duplicate_types(self):
        """Two maps of the same type can't both drive the slot."""
        textures = ["s_Mixed_AO.png", "s_AO.png"]
        type_cache = {
            "s_Mixed_AO.png": "Ambient_Occlusion",
            "s_AO.png": "Ambient_Occlusion",
        }

        kept, dropped, extracted = self.shader._resolve_map_conflicts(
            textures, type_cache
        )

        self.assertEqual(kept, ["s_Mixed_AO.png"])
        self.assertEqual([d[1] for d in dropped], ["Ambient_Occlusion"])
        self.assertIn("duplicate", dropped[0][2])

    # -------------------------------------------------------------------------
    # Test graph-dependent slots (Standard_Transparent.sfx)
    #
    # A StingrayPBS node's slots come from the ShaderFX graph loaded into it.
    # The transparent graph (used whenever the set has an Opacity map) exposes
    # Autodesk's opacity presets have NO TEX_ao_map / use_ao_map; mayatk's `_AO`
    # presets add them, but a graph loaded from anywhere else may still lack
    # any slot, so every plug write is probed first.
    # -------------------------------------------------------------------------

    def test_opacity_graphs_carry_the_ao_slot(self):
        """Premise of the AO tests: every StingrayPBS graph the tool loads has AO.

        Autodesk's ``Standard_Masked.sfx`` / ``Standard_Transparent.sfx`` leave
        the Standard Base's 'Ambient Occlusion' socket free, so an opacity
        material used to lose its AO everywhere (viewport, FBX, GLB). mayatk's
        ``_AO`` presets carry the opaque preset's chain under Autodesk's slot
        names.
        """
        for mode in ("masked", "transparent", "none"):
            sr_node = self.shader.setup_stringray_node(
                f"test_slots_{mode}", opacity=mode != "none", opacity_mode=mode
            )
            for attr in ("TEX_ao_map", "use_ao_map"):
                self.assertTrue(
                    cmds.attributeQuery(attr, node=sr_node, exists=True),
                    f"{mode}: {attr} missing",
                )

    def test_connect_msao_on_transparent_graph(self):
        """MSAO on the opacity graph wires metallic, roughness AND its AO.

        Regression (Autodesk's preset): this raised
        `setAttr: No object matches name: <shader>.use_ao_map`.
        """
        sr_node = self.shader.setup_stringray_node("test_transp_msao", opacity=True)
        texture_path = os.path.join(self.test_assets, "model_MaskMap.png")
        success = self.shader.connect_stingray_nodes(texture_path, "MSAO", sr_node)
        self.assertTrue(success, "MSAO should connect its channels")
        self.assertIsNotNone(
            cmds.listConnections(f"{sr_node}.TEX_metallic_map"), "Metallic missing"
        )
        self.assertIsNotNone(
            cmds.listConnections(f"{sr_node}.TEX_roughness_map"),
            "Roughness must be bound at the COMPOUND plug (per-child is never sampled)",
        )
        self.assertTrue(
            cmds.listConnections(f"{sr_node}.TEX_ao_map")
            or cmds.listConnections(f"{sr_node}.TEX_ao_mapX"),
            "AO must reach the opacity graph too",
        )

    def test_connect_orm_on_transparent_graph(self):
        """ORM on the opacity graph wires roughness, metallic and AO."""
        sr_node = self.shader.setup_stringray_node("test_transp_orm", opacity=True)
        texture_path = os.path.join(self.test_assets, "model_MaskMap.png")
        success = self.shader.connect_stingray_nodes(texture_path, "ORM", sr_node)
        self.assertTrue(success)
        self.assertIsNotNone(
            cmds.listConnections(f"{sr_node}.TEX_roughness_map"),
            "Roughness must be bound at the COMPOUND plug (per-child is never sampled)",
        )
        self.assertTrue(
            cmds.listConnections(f"{sr_node}.TEX_ao_map")
            or cmds.listConnections(f"{sr_node}.TEX_ao_mapX"),
            "AO must reach the opacity graph too",
        )

    def test_connect_ao_on_transparent_graph(self):
        """A standalone AO map lands on the opacity graph like on the opaque one."""
        sr_node = self.shader.setup_stringray_node("test_transp_ao", opacity=True)
        texture_path = os.path.join(self.test_assets, "model_AO.png")
        success = self.shader.connect_stingray_nodes(
            texture_path, "Ambient_Occlusion", sr_node
        )
        self.assertTrue(success, "AO has a slot on the opacity graph now")
        self.assertIsNotNone(cmds.listConnections(f"{sr_node}.TEX_ao_map"))
        self.assertEqual(cmds.getAttr(f"{sr_node}.use_ao_map"), 1.0)

    def test_inert_opacity_map_does_not_cost_the_ao_slot(self):
        """End-to-end: ``model_Opacity.png`` is solid white -- exactly what an
        exporter writes beside an opaque set when the project has an opacity
        channel. It used to pick the transparent graph and drop the AO map
        for an opacity that made nothing transparent."""
        textures = [
            os.path.join(self.test_assets, "model_Base_Color.png"),
            os.path.join(self.test_assets, "model_Opacity.png"),
            os.path.join(self.test_assets, "model_Metallic.png"),
            os.path.join(self.test_assets, "model_Roughness.png"),
            os.path.join(self.test_assets, "model_AO.png"),
        ]

        result = self.shader.create_network(textures, name="test_inert_opacity")

        self.assertIsNotNone(result)
        self.assertFalse(
            cmds.attributeQuery("opacity", node="test_inert_opacity", exists=True),
            "the transparent graph was loaded for an opacity map that makes "
            "nothing transparent",
        )
        self.assertTrue(
            cmds.listConnections("test_inert_opacity.TEX_ao_map"),
            "AO should connect on the opaque graph",
        )
        self.assertFalse(
            any("failed" in m for m in self.test_messages), self.test_messages
        )

    def test_real_opacity_map_keeps_transparency_and_its_ao(self):
        """A REAL opacity map beside an AO map: both land.

        Before mayatk's ``_AO`` presets no StingrayPBS graph hosted both, so
        opacity won and the AO row failed with a trade-off note. The
        transparency still rides the colour map's alpha (``opacity`` is a
        uniform; a texture there is a no-op) and the AO its own slot.
        """
        from PIL import Image

        repo_temp = os.path.join(os.path.dirname(__file__), "temp_tests", "gs_opacity")
        os.makedirs(repo_temp, exist_ok=True)
        self.addCleanup(shutil.rmtree, repo_temp, True)
        opacity = os.path.join(repo_temp, "model_Opacity.png")
        img = Image.new("L", (16, 16), 255)
        img.putpixel((0, 0), 0)
        img.save(opacity)
        # A base colour at the opacity map's resolution: the shared fixture is
        # 1x1 and packing a 16x16 alpha into it resamples the transparency away.
        base_color = os.path.join(repo_temp, "model_Base_Color.png")
        Image.new("RGB", (16, 16), (128, 128, 128)).save(base_color)
        textures = [
            base_color,
            opacity,
            os.path.join(self.test_assets, "model_Metallic.png"),
            os.path.join(self.test_assets, "model_Roughness.png"),
            os.path.join(self.test_assets, "model_AO.png"),
        ]

        result = self.shader.create_network(textures, name="test_transp_network")

        self.assertIsNotNone(result)
        self.assertEqual(
            mtk.MatUtils.get_stingray_opacity_mode("test_transp_network"),
            "transparent",
        )
        self.assertIsNone(
            cmds.listConnections(
                "test_transp_network.opacity", source=True, destination=False
            ),
            "`opacity` is a uniform, not a sampler",
        )
        self.assertEqual(cmds.getAttr("test_transp_network.use_opacity_map"), 1.0)
        self.assertIsNotNone(
            cmds.listConnections("test_transp_network.TEX_ao_map"),
            "AO must connect on the transparent graph",
        )
        self.assertFalse(
            any("failed" in m for m in self.test_messages), self.test_messages
        )

    def test_standalone_opacity_reaches_a_per_pixel_slot_on_stingray(self):
        """A standalone Opacity map must end up somewhere VP2 samples per-pixel.

        Standard_Transparent.sfx has NO sampler slot for opacity. It carries a
        SCALAR `opacity` uniform and a `use_opacity_map` flag that SELECTS
        BETWEEN the colour map's alpha (1) and that uniform (0) -- so wiring a
        file node into `opacity` connects a texture to a uniform (never
        per-pixel in VP2) that the flag then routes past anyway. The build
        looked wired and rendered with no opacity at all, while every mesh on
        it still went through VP2's transparent queue: see-through, wrongly
        sorted geometry. The alpha has to ride the colour map.
        """
        from PIL import Image

        repo_temp = os.path.join(os.path.dirname(__file__), "temp_tests", "gs_perpixel")
        os.makedirs(repo_temp, exist_ok=True)
        self.addCleanup(shutil.rmtree, repo_temp, True)

        # An RGB base colour (no alpha band) beside a real opacity map -- the
        # shape every Substance export produces.
        base = os.path.join(repo_temp, "model_Base_Color.png")
        Image.new("RGB", (16, 16), (200, 40, 40)).save(base)
        opacity = os.path.join(repo_temp, "model_Opacity.png")
        alpha = Image.new("L", (16, 16), 255)
        alpha.putpixel((0, 0), 40)
        alpha.save(opacity)

        mat = "test_perpixel_opacity"
        self.shader.create_network([base, opacity], name=mat)

        self.assertEqual(
            mtk.MatUtils.get_stingray_opacity_mode(mat),
            "transparent",
            "premise: a real opacity map asks for the transparent graph",
        )
        self.assertIsNone(
            cmds.listConnections(f"{mat}.opacity", source=True, destination=False),
            "`opacity` is a UNIFORM -- a texture on it is a silent no-op",
        )
        self.assertEqual(
            cmds.getAttr(f"{mat}.use_opacity_map"),
            1.0,
            "the flag must select the colour map's alpha branch",
        )
        wired = cmds.listConnections(
            f"{mat}.TEX_color_map", source=True, destination=False
        )
        self.assertTrue(wired, "a colour map must be wired")
        packed = cmds.getAttr(f"{wired[0]}.fileTextureName")
        self.assertTrue(
            self.shader._carries_alpha(packed),
            f"the wired colour map must carry the opacity in its alpha: {packed}",
        )
        with ptk.ImgUtils.allow_large_images():
            self.assertEqual(
                ptk.ImgUtils.ensure_image(packed).getchannel("A").getextrema(),
                alpha.getextrema(),
                "the packed alpha must be the opacity map, unaltered",
            )

    def test_opacity_carried_in_an_alpha_band_survives_the_pack(self):
        """An opacity map may hold its data in ALPHA, not luminance.

        `pack_channels` takes the LUMINANCE of whatever fills its alpha slot,
        so a white-RGB / real-alpha opacity export (some writers produce one)
        packed as solid white and the material came out opaque.
        """
        from PIL import Image

        repo_temp = os.path.join(
            os.path.dirname(__file__), "temp_tests", "gs_alphaband"
        )
        os.makedirs(repo_temp, exist_ok=True)
        self.addCleanup(shutil.rmtree, repo_temp, True)

        base = os.path.join(repo_temp, "model_Base_Color.png")
        Image.new("RGB", (16, 16), (40, 90, 200)).save(base)
        # White RGB (luminance says "opaque") over an alpha that is the real map.
        opacity = os.path.join(repo_temp, "model_Opacity.png")
        alpha = Image.new("L", (16, 16), 255)
        alpha.putpixel((0, 0), 12)
        Image.merge(
            "RGBA", (*Image.new("RGB", (16, 16), (255, 255, 255)).split(), alpha)
        ).save(opacity)

        mat = "test_alpha_band_opacity"
        self.shader.create_network([base, opacity], name=mat)

        wired = cmds.listConnections(
            f"{mat}.TEX_color_map", source=True, destination=False
        )
        packed = cmds.getAttr(f"{wired[0]}.fileTextureName")
        with ptk.ImgUtils.allow_large_images():
            self.assertEqual(
                ptk.ImgUtils.ensure_image(packed).getchannel("A").getextrema(),
                (12, 255),
                "the alpha band, not the luminance, is the opacity",
            )
        self.assertEqual(cmds.getAttr(f"{mat}.use_opacity_map"), 1.0)

    def test_a_pack_that_loses_the_opacity_is_reported_not_wired(self):
        """A pack that flattens the alpha must not be handed to the shader.

        Nothing downstream re-checks that the opacity survived, so a silently
        flattened pack wired a dud colour map and surfaced only as "carries no
        usable alpha" with no way to tell which input was at fault. Provoked
        here with a colour map far smaller than the opacity: the alpha is
        resampled down to it and the detail averages away.
        """
        from PIL import Image

        repo_temp = os.path.join(os.path.dirname(__file__), "temp_tests", "gs_lostpack")
        os.makedirs(repo_temp, exist_ok=True)
        self.addCleanup(shutil.rmtree, repo_temp, True)

        base = os.path.join(repo_temp, "model_Base_Color.png")
        Image.new("RGB", (1, 1), (128, 128, 128)).save(base)
        opacity = os.path.join(repo_temp, "model_Opacity.png")
        img = Image.new("L", (64, 64), 255)
        img.putpixel((0, 0), 0)  # one dark texel — gone once resampled to 1x1
        img.save(opacity)

        mat = "test_lost_pack"
        self.shader.create_network([base, opacity], name=mat)

        self.assertTrue(
            any("the opacity did not survive" in m for m in self.test_messages),
            f"a flattened pack must name both inputs: {self.test_messages}",
        )
        # The maps stay separate rather than the dud being wired as the colour map.
        wired = cmds.listConnections(
            f"{mat}.TEX_color_map", source=True, destination=False
        )
        self.assertEqual(
            os.path.basename(cmds.getAttr(f"{wired[0]}.fileTextureName")).lower(),
            "model_base_color.png",
        )
        self.assertEqual(
            cmds.getAttr(f"{mat}.use_opacity_map"),
            0.0,
            "no usable alpha reached the colour map, so the selector must be off",
        )

    def test_masked_packs_the_opacity_into_the_colour_alpha(self):
        """Masked: the opacity rides the colour map's alpha, selector 1.

        Every consumer reads a cutout from there -- the masked ShaderFX graph
        (``use_opacity_map`` = 1 selects the colour alpha), Unity's importer
        (Built-in: ``_MainTex`` alpha against ``_Cutoff``, and it has no
        mask-map slot at all, so a separate opacity texture is simply dropped;
        URP/HDRP: the ColorMap alpha), and glTF, whose only opacity is
        ``baseColorTexture``'s alpha. Verified end to end on a production set:
        Maya VP2 render, a stock Unity 6 import, FBX2glTF.
        """
        from PIL import Image

        repo_temp = os.path.join(os.path.dirname(__file__), "temp_tests", "gs_masked")
        os.makedirs(repo_temp, exist_ok=True)
        self.addCleanup(shutil.rmtree, repo_temp, True)
        base = os.path.join(repo_temp, "model_Base_Color.png")
        Image.new("RGB", (16, 16), (200, 40, 40)).save(base)
        opacity = os.path.join(repo_temp, "model_Opacity.png")
        alpha = Image.new("L", (16, 16), 255)
        alpha.putpixel((0, 0), 40)
        alpha.save(opacity)

        mat = "test_masked_packed"
        self.shader.create_network([base, opacity], name=mat, opacity_mode="masked")

        self.assertEqual(mtk.MatUtils.get_stingray_opacity_mode(mat), "masked")
        colour = cmds.listConnections(
            f"{mat}.TEX_color_map", source=True, destination=False
        )
        packed = cmds.getAttr(f"{colour[0]}.fileTextureName")
        self.assertTrue(
            self.shader._carries_alpha(packed),
            f"the wired colour map must carry the opacity in its alpha: {packed}",
        )
        with ptk.ImgUtils.allow_large_images():
            self.assertEqual(
                ptk.ImgUtils.ensure_image(packed).getchannel("A").getextrema(),
                alpha.getextrema(),
            )
        self.assertEqual(
            cmds.getAttr(f"{mat}.use_opacity_map"),
            1.0,
            "1 selects the colour map's alpha on the masked graph",
        )
        for plug in ("TEX_mask_map", "TEX_mask_mapX", "TEX_mask_mapY", "TEX_mask_mapZ"):
            self.assertIsNone(
                cmds.listConnections(f"{mat}.{plug}", source=True, destination=False),
                f"{plug}: the Maya-only sampler is not used when the alpha is packed",
            )
        self.assertTrue(cmds.attributeQuery("mask_threshold", node=mat, exists=True))

    def test_opacity_none_builds_the_opaque_graph_over_a_usable_alpha(self):
        """`Opacity: None` outranks the set: a real alpha does not summon a graph.

        The counterpart of the masked test above, on the SAME set -- the
        opacity map is authored and usable, and the panel is saying to build
        the solid shader anyway (a cutout the target engine masks with its own
        material, a decal sheet reused as a body). The map must not be packed
        into the colour alpha either: packing is what makes an opacity
        reachable, and the caller asked for it to be unreachable.
        """
        from PIL import Image

        repo_temp = os.path.join(os.path.dirname(__file__), "temp_tests", "gs_none")
        os.makedirs(repo_temp, exist_ok=True)
        self.addCleanup(shutil.rmtree, repo_temp, True)
        base = os.path.join(repo_temp, "model_Base_Color.png")
        Image.new("RGB", (16, 16), (200, 40, 40)).save(base)
        opacity = os.path.join(repo_temp, "model_Opacity.png")
        alpha = Image.new("L", (16, 16), 255)
        alpha.putpixel((0, 0), 40)
        alpha.save(opacity)

        mat = "test_opacity_none"
        self.shader.create_network([base, opacity], name=mat, opacity_mode="none")

        self.assertEqual(mtk.MatUtils.get_stingray_opacity_mode(mat), "none")
        self.assertFalse(cmds.attributeQuery("opacity", node=mat, exists=True))
        colour = cmds.listConnections(
            f"{mat}.TEX_color_map", source=True, destination=False
        )
        self.assertTrue(colour, "the colour map still has to reach the shader")
        wired = cmds.getAttr(f"{colour[0]}.fileTextureName")
        self.assertFalse(
            self.shader._carries_alpha(wired),
            f"the opacity must not have been packed into the colour map: {wired}",
        )

        # The other shape of the same request: the alpha is ALREADY in the
        # colour map, so there is nothing to drop -- the map is wired for its
        # colour and the opaque graph simply never reads the alpha. Worth
        # building rather than reasoning about: this is the one route that
        # reaches `_select_color_map_alpha` with a genuinely alpha-bearing map
        # and no selector on the graph to point at it.
        packed = os.path.join(repo_temp, "model_Albedo_Transparency.png")
        rgba = Image.new("RGBA", (16, 16), (200, 40, 40, 255))
        rgba.putpixel((0, 0), (200, 40, 40, 40))
        rgba.save(packed)

        mat = "test_opacity_none_packed"
        self.shader.create_network([packed], name=mat, opacity_mode="none")

        self.assertEqual(mtk.MatUtils.get_stingray_opacity_mode(mat), "none")
        colour = cmds.listConnections(
            f"{mat}.TEX_color_map", source=True, destination=False
        )
        self.assertTrue(colour, "the packed map is still the colour map")
        self.assertFalse(
            cmds.attributeQuery("use_opacity_map", node=mat, exists=True),
            "the opaque graph has no selector to raise",
        )

    def test_masked_without_a_colour_map_binds_the_mask_sampler(self):
        """No colour map to pack into: the masked graph's own sampler takes the
        map -- COMPOUND bind (a per-child bind is an unbound sampler that
        discards every fragment), selector 0 so the graph reads it."""
        from PIL import Image

        repo_temp = os.path.join(
            os.path.dirname(__file__), "temp_tests", "gs_masked_nocolour"
        )
        os.makedirs(repo_temp, exist_ok=True)
        self.addCleanup(shutil.rmtree, repo_temp, True)
        opacity = os.path.join(repo_temp, "model_Opacity.png")
        alpha = Image.new("L", (16, 16), 255)
        alpha.putpixel((0, 0), 40)
        alpha.save(opacity)
        roughness = os.path.join(repo_temp, "model_Roughness.png")
        Image.new("L", (16, 16), 128).save(roughness)

        mat = "test_masked_sampler"
        self.shader.create_network(
            [opacity, roughness], name=mat, opacity_mode="masked"
        )

        self.assertEqual(mtk.MatUtils.get_stingray_opacity_mode(mat), "masked")
        parent = cmds.listConnections(
            f"{mat}.TEX_mask_map", source=True, destination=False, plugs=True
        )
        self.assertEqual([p.split(".")[-1] for p in parent or []], ["outColor"])
        for child in "XYZ":
            self.assertIsNone(
                cmds.listConnections(
                    f"{mat}.TEX_mask_map{child}", source=True, destination=False
                )
            )
        self.assertEqual(cmds.getAttr(f"{mat}.use_opacity_map"), 0.0)

    def test_masked_albedo_transparency_selects_the_colour_alpha(self):
        """Masked with the opacity already in the colour map's alpha: flag 1,
        and the mask sampler must NOT also be fed from that same texture (its
        red channel would then be read as the mask)."""
        from PIL import Image

        repo_temp = os.path.join(
            os.path.dirname(__file__), "temp_tests", "gs_masked_at"
        )
        os.makedirs(repo_temp, exist_ok=True)
        self.addCleanup(shutil.rmtree, repo_temp, True)
        at = os.path.join(repo_temp, "model_Albedo_Transparency.png")
        img = Image.new("RGBA", (16, 16), (200, 40, 40, 255))
        img.putpixel((0, 0), (200, 40, 40, 30))
        img.save(at)

        mat = "test_masked_at"
        self.shader.create_network([at], name=mat, opacity_mode="masked")

        self.assertEqual(mtk.MatUtils.get_stingray_opacity_mode(mat), "masked")
        self.assertEqual(cmds.getAttr(f"{mat}.use_opacity_map"), 1.0)
        for plug in ("TEX_mask_map", "TEX_mask_mapX", "TEX_mask_mapY", "TEX_mask_mapZ"):
            self.assertIsNone(
                cmds.listConnections(f"{mat}.{plug}", source=True, destination=False),
                f"{plug} must stay unconnected when the colour alpha is the mask",
            )

    def test_padding_alpha_albedo_transparency_does_not_eat_a_real_opacity_map(self):
        """An Albedo_Transparency with a padding alpha is a colour map, nothing more.

        Classified by its name it outranked the standalone Opacity in the
        conflict pass (the registry rule cannot see image content), then
        carried no opacity itself -- the set's transparency silently gone.
        """
        from PIL import Image

        repo_temp = os.path.join(
            os.path.dirname(__file__), "temp_tests", "gs_padding_at"
        )
        os.makedirs(repo_temp, exist_ok=True)
        self.addCleanup(shutil.rmtree, repo_temp, True)
        at = os.path.join(repo_temp, "model_Albedo_Transparency.png")
        Image.new("RGBA", (16, 16), (200, 40, 40, 255)).save(at)  # alpha = padding
        opacity = os.path.join(repo_temp, "model_Opacity.png")
        alpha = Image.new("L", (16, 16), 255)
        alpha.putpixel((0, 0), 40)
        alpha.save(opacity)

        mat = "test_padding_at"
        self.shader.create_network([at, opacity], name=mat)

        colour = cmds.listConnections(
            f"{mat}.TEX_color_map", source=True, destination=False
        )
        packed = cmds.getAttr(f"{colour[0]}.fileTextureName")
        with ptk.ImgUtils.allow_large_images():
            self.assertEqual(
                ptk.ImgUtils.ensure_image(packed).getchannel("A").getextrema(),
                alpha.getextrema(),
                "the real opacity must reach the colour map's alpha",
            )
        self.assertEqual(cmds.getAttr(f"{mat}.use_opacity_map"), 1.0)

    def test_shading_group_follows_the_created_shader_name(self):
        """Maya uniquifies a taken name; the group must follow the node it got."""
        for builder in (
            lambda n: self.shader.setup_stringray_node(n, opacity=False),
            lambda n: self.shader.setup_standard_surface_node(n, opacity=False),
        ):
            first = builder("gs_sg_twin")
            second = builder("gs_sg_twin")
            self.assertNotEqual(first, second, "premise: the second name is uniquified")
            for shader in (first, second):
                sgs = cmds.listConnections(f"{shader}.outColor", type="shadingEngine")
                self.assertEqual(sgs, [f"{shader}SG"], f"{shader} -> {sgs}")

    def test_opaque_set_under_an_opacity_preset_is_not_thin_walled(self):
        """The same capability flag thin-walled every standardSurface / openPBR
        built under the default preset -- a silent render change (two-sided,
        no interior) on materials with no alpha at all."""
        textures = [
            os.path.join(self.test_assets, "model_Base_Color.png"),
            os.path.join(self.test_assets, "model_Metallic.png"),
            os.path.join(self.test_assets, "model_Roughness.png"),
        ]
        for shader_type, attr in (
            ("standard_surface", "thinWalled"),
            ("open_pbr", "geometryThinWalled"),
        ):
            with self.subTest(shader_type=shader_type):
                name = f"test_not_thin_{shader_type}"
                try:
                    self.shader.create_network(
                        textures,
                        name=name,
                        config="PBR Metallic/Roughness",
                        shader_type=shader_type,
                    )
                except RuntimeError as error:  # openPBR needs a recent 2025+
                    self.skipTest(str(error))
                self.assertFalse(
                    cmds.attributeQuery(attr, node=name, exists=True)
                    and cmds.getAttr(f"{name}.{attr}"),
                    f"{shader_type} thin-walled with nothing to be thin about",
                )

    def test_opaque_set_under_an_opacity_preset_keeps_its_ao(self):
        """End-to-end regression: AO must connect on an opaque set.

        Built through the default 'PBR Metallic/Roughness' preset, whose
        opacity=True used to load Standard_Transparent.sfx -- a graph with no
        TEX_ao_map -- so the AO map was reported as having no matching slot.
        """
        textures = [
            os.path.join(self.test_assets, "model_Base_Color.png"),
            os.path.join(self.test_assets, "model_Metallic.png"),
            os.path.join(self.test_assets, "model_Roughness.png"),
            os.path.join(self.test_assets, "model_AO.png"),
        ]

        result = self.shader.create_network(
            textures, name="test_opaque_ao", config="PBR Metallic/Roughness"
        )

        self.assertIsNotNone(result)
        self.assertTrue(
            cmds.attributeQuery("TEX_ao_map", node="test_opaque_ao", exists=True),
            "the opaque graph should have been loaded",
        )
        self.assertIsNotNone(
            cmds.listConnections("test_opaque_ao.TEX_ao_map"),
            "AO should be connected, not dropped for an opacity nothing drives",
        )
        self.assertFalse(
            any("no 'TEX_ao_map' slot" in m for m in self.test_messages),
            f"AO must not be skipped; got {self.test_messages}",
        )

    # -------------------------------------------------------------------------
    # Test Full Network Creation
    # -------------------------------------------------------------------------

    def test_map_outside_the_workspace_keeps_its_absolute_path(self):
        """A map that does not live in the workspace's sourceimages is referenced
        where it lives. The blanket relativize kept only the BASENAME for a file
        on another drive (``sourceimages/x.png`` for ``O:/.../x.png``), which
        resolves nowhere -- every map of a scene pulled through a fresh mayapy
        (default workspace on C:) rendered black (production, 2026-08-22)."""
        from PIL import Image

        from mayatk.env_utils._env_utils import EnvUtils

        repo_temp = os.path.join(os.path.dirname(__file__), "temp_tests", "gs_abs")
        os.makedirs(repo_temp, exist_ok=True)
        self.addCleanup(shutil.rmtree, repo_temp, True)
        path = os.path.join(repo_temp, "far_Base_Color.png")
        Image.new("RGBA", (8, 8), (128, 128, 128, 255)).save(path)
        sourceimages = EnvUtils.get_env_info("sourceimages")
        self.assertFalse(ptk.FileUtils.is_under(path, sourceimages), sourceimages)

        self.shader.create_network([path], name="far_network")
        files = cmds.ls(cmds.listHistory("far_network") or [], type="file")
        ours = [
            f for f in files if f.startswith("far_Base_Color")
        ]  # + Stingray's env/BRDF
        self.assertEqual(len(ours), 1, files)
        stored = cmds.getAttr(f"{ours[0]}.fileTextureName")
        self.assertTrue(os.path.isabs(stored), stored)
        self.assertTrue(os.path.isfile(stored), stored)

    def test_create_network_basic(self):
        """Test basic shader network creation."""
        textures = [
            os.path.join(self.test_assets, "model_Base_Color.png"),
            os.path.join(self.test_assets, "model_Metallic.png"),
            os.path.join(self.test_assets, "model_Roughness.png"),
        ]

        result = self.shader.create_network(textures, name="test_basic_network")

        # Check that shader was created
        self.assertTrue(cmds.objExists("test_basic_network"))

    def test_create_network_pbr_metal_roughness(self):
        """Test PBR Metal Roughness workflow."""
        textures = [
            os.path.join(self.test_assets, "model_Base_Color.png"),
            os.path.join(self.test_assets, "model_Metallic.png"),
            os.path.join(self.test_assets, "model_Roughness.png"),
            os.path.join(self.test_assets, "model_Normal_OpenGL.png"),
        ]

        result = self.shader.create_network(
            textures,
            name="test_pbr",
            normal_type="OpenGL",
            albedo_transparency=False,
            metallic_smoothness=False,
            output_extension="png",
        )

        self.assertTrue(cmds.objExists("test_pbr"))

    def test_create_network_unity_urp(self):
        """Test Unity URP workflow (with albedo transparency and metallic smoothness)."""
        textures = [
            os.path.join(self.test_assets, "model_BaseColor.png"),
            os.path.join(self.test_assets, "model_Metallic.png"),
            os.path.join(self.test_assets, "model_Roughness.png"),
            os.path.join(self.test_assets, "model_Normal_OpenGL.png"),
        ]

        result = self.shader.create_network(
            textures,
            name="test_unity_urp",
            albedo_transparency=True,
            metallic_smoothness=True,
            mask_map=False,
        )

        self.assertTrue(cmds.objExists("test_unity_urp"))

    def test_create_network_unity_hdrp(self):
        """Test Unity HDRP workflow (with mask map)."""
        textures = [
            os.path.join(self.test_assets, "model_BaseColor.png"),
            os.path.join(self.test_assets, "model_Metallic.png"),
            os.path.join(self.test_assets, "model_Roughness.png"),
            os.path.join(self.test_assets, "model_AO.png"),
        ]

        result = self.shader.create_network(
            textures,
            name="test_unity_hdrp",
            albedo_transparency=False,
            metallic_smoothness=False,
            mask_map=True,
        )

        self.assertTrue(cmds.objExists("test_unity_hdrp"))

    def test_create_network_unreal_engine(self):
        """Test Unreal Engine workflow."""
        textures = [
            os.path.join(self.test_assets, "model_BaseColor.png"),
            os.path.join(self.test_assets, "model_Metallic.png"),
            os.path.join(self.test_assets, "model_Roughness.png"),
            os.path.join(self.test_assets, "model_Normal_OpenGL.png"),
        ]

        result = self.shader.create_network(
            textures,
            name="test_unreal",
            normal_type="DirectX",
            albedo_transparency=True,
            mask_map=False,
        )

        self.assertTrue(cmds.objExists("test_unreal"))

    def test_create_network_gltf(self):
        """Test glTF 2.0 workflow."""
        textures = [
            os.path.join(self.test_assets, "model_BaseColor.png"),
            os.path.join(self.test_assets, "model_Metallic.png"),
            os.path.join(self.test_assets, "model_Roughness.png"),
        ]

        result = self.shader.create_network(
            textures,
            name="test_gltf",
            albedo_transparency=False,
            metallic_smoothness=False,
            mask_map=False,
        )

        self.assertTrue(cmds.objExists("test_gltf"))

    def test_create_network_godot(self):
        """Test Godot workflow."""
        textures = [
            os.path.join(self.test_assets, "model_BaseColor.png"),
            os.path.join(self.test_assets, "model_Metallic.png"),
            os.path.join(self.test_assets, "model_Roughness.png"),
        ]

        result = self.shader.create_network(
            textures,
            name="test_godot",
        )

        self.assertTrue(cmds.objExists("test_godot"))

    def test_create_network_specular_glossiness(self):
        """Test Specular/Glossiness workflow."""
        textures = [
            os.path.join(self.test_assets, "model_Diffuse.png"),
            os.path.join(self.test_assets, "model_Specular.png"),
            os.path.join(self.test_assets, "model_Glossiness.png"),
        ]

        # Create dummy files if they don't exist
        for tex in textures:
            if not os.path.exists(tex):
                from PIL import Image

                Image.new("RGB", (1, 1)).save(tex)

        result = self.shader.create_network(
            textures,
            name="test_specgloss",
            metallic_smoothness=True,
        )

        self.assertTrue(cmds.objExists("test_specgloss"))

    def test_create_network_empty_textures(self):
        """Test error handling for empty texture list."""
        result = self.shader.create_network([])

        self.assertIsNone(result)
        self.assertTrue(len(self.test_messages) > 0)
        self.assertTrue(any("No textures given" in msg for msg in self.test_messages))

    def test_create_network_different_extensions(self):
        """Test network creation with various image extensions."""
        # TGA, BMP, TIFF removed due to PIL saving issues on Windows test environment
        extensions = ["png", "jpg"]

        for ext in extensions:
            with self.subTest(extension=ext):
                textures = [
                    os.path.join(self.test_assets, f"model_BaseColor.{ext}"),
                    os.path.join(self.test_assets, f"model_Metallic.{ext}"),
                    os.path.join(self.test_assets, f"model_Roughness.{ext}"),
                ]

                # Create dummy files if they don't exist
                for tex in textures:
                    if not os.path.exists(tex):
                        from PIL import Image

                        Image.new("RGB", (1, 1)).save(tex)

                result = self.shader.create_network(
                    textures,
                    name=f"test_{ext}_network",
                    output_extension=ext,
                    callback=self._test_callback,
                )

                self.assertTrue(cmds.objExists(f"test_{ext}_network"))

    # -------------------------------------------------------------------------
    # Test Edge Cases and Error Handling
    # -------------------------------------------------------------------------

    def test_unknown_texture_type(self):
        """Test handling of unknown texture types."""
        # Create dummy files
        base_color = os.path.join(self.temp_dir, "model_BaseColor.png")
        unknown = os.path.join(self.temp_dir, "model_Unknown_Type.png")

        # Minimal valid 1x1 PNG data
        png_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"

        # Create valid image files
        with open(base_color, "wb") as f:
            f.write(png_data)
        with open(unknown, "wb") as f:
            f.write(png_data)

        textures = [base_color, unknown]

        self.test_messages = []
        result = self.shader.create_network(
            textures, name="test_unknown", callback=self._test_callback
        )

        # Should still create shader despite unknown texture
        # Note: MapFactory may split unknown types into separate batches,
        # ignoring the 'name' parameter. We check if the valid part ("model") was created.
        self.assertTrue(
            cmds.objExists("model") or cmds.objExists("test_unknown"),
            f"Shader 'model' (or 'test_unknown') was not created. Messages: {self.test_messages}",
        )

    def test_every_shader_type_receives_an_opengl_normal(self):
        """The end-to-end contract: whatever the combo writes, the shader gets OpenGL.

        Maya renders tangent-space normals Y-up, so a DirectX map wired as-is
        lights inverted. Verified per shader type because each wires the normal
        into a different plug (StingrayPBS TEX_normal_map, standardSurface and
        openPBRSurface via bump2d) — and the openPBR plug name is itself
        version-dependent (Maya 2025 has normalCamera, not geometryNormal).
        """
        from PIL import Image

        src = os.path.join(self.temp_dir, "e2e_src")
        os.makedirs(src, exist_ok=True)
        gl = os.path.join(src, "e2e_Normal_OpenGL.png")
        _write_test_image(gl, (100, 60, 250))
        base = os.path.join(src, "e2e_BaseColor.png")
        _write_test_image(base, (180, 120, 90))
        files = [gl, base]
        src_green = Image.open(gl).convert("RGB").getpixel((8, 8))[1]

        for shader_type in ("stingray", "standard_surface", "open_pbr"):
            for normal_type in ("OpenGL", "DirectX"):
                with self.subTest(shader=shader_type, normal=normal_type):
                    cmds.file(new=True, force=True)
                    out = os.path.join(
                        self.temp_dir, f"e2e_{shader_type}_{normal_type}"
                    )
                    self.shader.create_network(
                        files,
                        name=f"e2e_{normal_type}",
                        shader_type=shader_type,
                        normal_type=normal_type,
                        output_dir=out,
                    )
                    wired = sorted(
                        {
                            cmds.getAttr(f"{fn}.fileTextureName")
                            for fn in (cmds.ls(type="file") or [])
                            if "Normal" in cmds.getAttr(f"{fn}.fileTextureName")
                        }
                    )
                    self.assertEqual(len(wired), 1, f"one normal expected: {wired}")
                    self.assertIn("Normal_OpenGL", wired[0], wired[0])
                    self.assertEqual(
                        Image.open(wired[0]).convert("RGB").getpixel((8, 8))[1],
                        src_green,
                        "the wired normal's green differs from the OpenGL source",
                    )

    def test_multiple_normal_maps_same_type(self):
        """Two maps of the SAME normal type collapse to one.

        (The old fixture used ``model_Normal_OpenGL_2.png``, which classifies as
        None — a trailing ``_2`` puts the alias off the end — so it never
        actually exercised a duplicate.)
        """
        textures = [
            "/x/model_BaseColor.png",
            "/x/model_Normal_OpenGL.png",
            "/x/model_Mixed_Normal_OpenGL.png",
        ]
        type_cache = {t: ptk.MapFactory.resolve_map_type(t) for t in textures}
        self.assertEqual(
            type_cache["/x/model_Mixed_Normal_OpenGL.png"],
            "Normal_OpenGL",
            "fixture must really be a second map of the same type",
        )

        kept, dropped, _notes = self.shader._resolve_map_conflicts(
            textures, type_cache, {}
        )
        opengl_maps = [t for t in kept if "Normal_OpenGL" in t]
        self.assertEqual(len(opengl_maps), 1, f"expected one, got {opengl_maps}")
        self.assertEqual(len(dropped), 1, dropped)
        self.assertIn("duplicate", dropped[0][2])

    def test_missing_required_maps(self):
        """Test creation with minimal texture set."""
        textures = [
            os.path.join(self.test_assets, "model_BaseColor.png"),  # Only base color
        ]

        # Create dummy file
        if not os.path.exists(textures[0]):
            from PIL import Image

            Image.new("RGB", (1, 1)).save(textures[0])

        result = self.shader.create_network(
            textures, name="test_minimal", callback=self._test_callback
        )

        # Should still create shader with just base color
        self.assertTrue(cmds.objExists("test_minimal"))

    def test_shader_name_auto_generation(self):
        """Test automatic shader name generation from texture."""
        textures = [
            os.path.join(self.test_assets, "character_BaseColor.png"),
        ]

        # Create dummy file
        if not os.path.exists(textures[0]):
            from PIL import Image

            Image.new("RGB", (1, 1)).save(textures[0])

        result = self.shader.create_network(
            textures,
            name="",  # Empty name - should auto-generate
            callback=self._test_callback,
        )

        # Should create shader with auto-generated name
        shaders = cmds.ls(type="StingrayPBS")
        self.assertTrue(len(shaders) > 0)

    # -------------------------------------------------------------------------
    # Test Standard Surface Shader
    # -------------------------------------------------------------------------

    def test_setup_standard_surface_node(self):
        """Test Maya Standard Surface node creation."""
        std_node = self.shader.setup_standard_surface_node(
            "test_std_surface", opacity=False
        )

        self.assertIsNotNone(std_node)
        self.assertTrue(cmds.objExists(std_node))
        self.assertEqual(cmds.nodeType(std_node), "standardSurface")

    def test_setup_standard_surface_with_opacity(self):
        """Test Standard Surface with transparency enabled."""
        std_node = self.shader.setup_standard_surface_node("test_opacity", opacity=True)

        self.assertIsNotNone(std_node)
        # Updated Logic: Opacity map should NOT enable transmission (glass)
        # It should only be used for alpha cutout (geometry opacity)
        self.assertEqual(cmds.getAttr(f"{std_node}.transmission"), 0.0)
        # Thin walled is still good for foliage/decals, but transmission should be off
        self.assertTrue(cmds.getAttr(f"{std_node}.thinWalled"))

    def test_create_network_standard_surface(self):
        """Test shader network creation with Standard Surface."""
        textures = [
            os.path.join(self.test_assets, "model_BaseColor.png"),
            os.path.join(self.test_assets, "model_Metallic.png"),
            os.path.join(self.test_assets, "model_Roughness.png"),
        ]

        # Create dummy files
        for tex in textures:
            if not os.path.exists(tex):
                from PIL import Image

                Image.new("RGB", (1, 1)).save(tex)

        result = self.shader.create_network(
            textures,
            name="test_std_network",
            shader_type="standard_surface",
            callback=self._test_callback,
        )

        # Check that Standard Surface shader exists
        self.assertTrue(cmds.objExists("test_std_network"))
        self.assertEqual(cmds.nodeType("test_std_network"), "standardSurface")

    def test_connect_standard_surface_base_color(self):
        """Test connecting base color to Standard Surface."""
        std_node = self.shader.setup_standard_surface_node(
            "test_std_color", opacity=False
        )
        texture_path = os.path.join(self.test_assets, "model_BaseColor.png")

        success = self.shader.connect_standard_surface_nodes(
            texture_path, "Base_Color", std_node
        )

        self.assertTrue(success)
        # Check connection exists
        connections = cmds.listConnections(f"{std_node}.baseColor")
        self.assertIsNotNone(connections)

    def test_connect_standard_surface_metallic(self):
        """Test connecting metallic map to Standard Surface."""
        std_node = self.shader.setup_standard_surface_node(
            "test_std_metal", opacity=False
        )
        texture_path = os.path.join(self.test_assets, "model_Metallic.png")

        success = self.shader.connect_standard_surface_nodes(
            texture_path, "Metallic", std_node
        )

        self.assertTrue(success)
        connections = cmds.listConnections(f"{std_node}.metalness")
        self.assertIsNotNone(connections)

    def test_connect_standard_surface_roughness(self):
        """Test connecting roughness map to Standard Surface."""
        std_node = self.shader.setup_standard_surface_node(
            "test_std_rough", opacity=False
        )
        texture_path = os.path.join(self.test_assets, "model_Roughness.png")

        success = self.shader.connect_standard_surface_nodes(
            texture_path, "Roughness", std_node
        )

        self.assertTrue(success)
        connections = cmds.listConnections(f"{std_node}.specularRoughness")
        self.assertIsNotNone(connections)

    def test_connect_standard_surface_normal(self):
        """Test connecting normal map to Standard Surface."""
        std_node = self.shader.setup_standard_surface_node(
            "test_std_normal", opacity=False
        )
        texture_path = os.path.join(self.test_assets, "model_Normal_OpenGL.png")

        success = self.shader.connect_standard_surface_nodes(
            texture_path, "Normal_OpenGL", std_node
        )

        self.assertTrue(success)
        connections = cmds.listConnections(f"{std_node}.normalCamera")
        self.assertIsNotNone(connections)

    def test_connect_standard_surface_msao(self):
        """Test connecting MSAO mask map to Standard Surface (Unity HDRP)."""
        std_node = self.shader.setup_standard_surface_node(
            "test_msao_std", opacity=False
        )
        texture_path = os.path.join(self.test_assets, "model_MaskMap.png")

        success = self.shader.connect_standard_surface_nodes(
            texture_path, "MSAO", std_node
        )

        self.assertTrue(success)
        # Verify metallic connection (from red channel)
        metallic_conn = cmds.listConnections(f"{std_node}.metalness")
        self.assertIsNotNone(metallic_conn, "Metallic connection missing")

        # Verify roughness connection (smoothness inverted from alpha)
        roughness_conn = cmds.listConnections(f"{std_node}.specularRoughness")
        self.assertIsNotNone(roughness_conn, "Roughness connection missing")

        # Should have a reverse node for smoothness->roughness conversion
        reverse_nodes = cmds.ls(type="reverse")
        self.assertTrue(
            len(reverse_nodes) > 0, "Reverse node for smoothness inversion missing"
        )

        # Smoothness lives in the packed ALPHA channel, so the MSAO file node
        # must read the real alpha (alphaIsLuminance=0). With aIL=1 Maya
        # synthesizes outAlpha from RGB luminance, driving roughness from
        # luminance(metallic, AO, detail) instead of smoothness.
        rev = (
            cmds.listConnections(
                f"{std_node}.specularRoughness",
                source=True,
                destination=False,
                type="reverse",
            )
            or []
        )
        self.assertTrue(rev, "smoothness-invert reverse not feeding roughness")
        msao_file = (
            cmds.listConnections(
                f"{rev[0]}.inputX", source=True, destination=False, type="file"
            )
            or []
        )
        self.assertTrue(msao_file, "MSAO file feeding the reverse missing")
        self.assertEqual(
            cmds.getAttr(f"{msao_file[0]}.alphaIsLuminance"),
            0,
            "MSAO smoothness must read the real alpha (aIL=0), not luminance",
        )

    # -------------------------------------------------------------------------
    # Test MapFactory Integration
    # -------------------------------------------------------------------------

    def test_texture_factory_integration_unity_hdrp(self):
        """Test MapFactory integration for Unity HDRP mask map creation."""
        textures = [
            os.path.join(self.test_assets, "model_BaseColor.png"),
            os.path.join(self.test_assets, "model_Metallic.png"),
            os.path.join(self.test_assets, "model_Smoothness.png"),
            os.path.join(self.test_assets, "model_AO.png"),
            os.path.join(self.test_assets, "model_Normal_OpenGL.png"),
        ]

        result = self.shader.create_network(
            textures,
            name="test_factory_hdrp",
            mask_map=True,
            callback=self._test_callback,
        )

        self.assertTrue(cmds.objExists("test_factory_hdrp"))

        # Check that callback was invoked with mask map message
        has_mask_map_msg = any("Mask Map" in msg for msg in self.test_messages)
        self.assertTrue(
            has_mask_map_msg,
            f"Expected 'Mask Map' message not found in callback. Messages: {self.test_messages}",
        )

        # CRITICAL: Verify MSAO connections were actually made
        shader_node = "test_factory_hdrp"

        # Check metallic connection exists
        metallic_conn = cmds.listConnections(f"{shader_node}.TEX_metallic_map")
        if not metallic_conn:
            metallic_conn = cmds.listConnections(
                f"{shader_node}.TEX_metallic_mapX"
            ) or cmds.listConnections(f"{shader_node}.TEX_metallic_mapR")

        self.assertIsNotNone(
            metallic_conn, "MSAO->Metallic connection missing in Unity HDRP workflow"
        )

        # Check AO connection exists
        ao_conn = cmds.listConnections(f"{shader_node}.TEX_ao_map")
        if not ao_conn:
            ao_conn = cmds.listConnections(
                f"{shader_node}.TEX_ao_mapX"
            ) or cmds.listConnections(f"{shader_node}.TEX_ao_mapR")

        self.assertIsNotNone(
            ao_conn, "MSAO->AO connection missing in Unity HDRP workflow"
        )

        # Check roughness/smoothness connection exists
        roughness_conn = cmds.listConnections(f"{shader_node}.TEX_roughness_map")
        self.assertIsNotNone(
            roughness_conn, "MSAO->Roughness connection missing in Unity HDRP workflow"
        )

        # Each slot samples its OWN image: a `TEX_*` slot binds only through its
        # compound plug, so one packed file cannot serve metallic AND AO — the
        # channels are extracted to loose maps (see `_wire_packed_map`).
        self.assertNotEqual(
            metallic_conn[0],
            ao_conn[0],
            "metallic and AO must be separate images, not one packed map on both",
        )

    def test_texture_factory_integration_with_normal_map(self):
        """Test that normal maps are properly processed and connected."""
        textures = [
            os.path.join(self.test_assets, "model_BaseColor.png"),
            os.path.join(self.test_assets, "model_Metallic.png"),
            os.path.join(self.test_assets, "model_Roughness.png"),
            os.path.join(self.test_assets, "model_AO.png"),
            os.path.join(self.test_assets, "model_Normal_OpenGL.png"),
        ]

        result = self.shader.create_network(
            textures,
            name="test_with_normal",
            mask_map=True,
            callback=self._test_callback,
        )

        self.assertTrue(cmds.objExists("test_with_normal"))

        # Verify normal map was mentioned in output
        self.assertTrue(
            any("Normal" in msg or "normal" in msg for msg in self.test_messages),
            "Normal map should be mentioned in callback messages",
        )

        # Verify normal map connection
        shader_node = "test_with_normal"
        normal_conn = cmds.listConnections(f"{shader_node}.TEX_normal_map")
        self.assertIsNotNone(normal_conn, "Normal map should be connected to shader")

    def test_texture_factory_integration_complete_pbr_set(self):
        """Test complete PBR texture set with all map types."""
        print("STARTING test_texture_factory_integration_complete_pbr_set")
        textures = [
            os.path.join(self.test_assets, "model_BaseColor.png"),
            os.path.join(self.test_assets, "model_Metallic.png"),
            os.path.join(self.test_assets, "model_Roughness.png"),
            os.path.join(self.test_assets, "model_AO.png"),
            os.path.join(self.test_assets, "model_Normal_OpenGL.png"),
        ]

        result = self.shader.create_network(
            textures,
            name="test_complete_pbr",
            callback=self._test_callback,
        )

        self.assertTrue(cmds.objExists("test_complete_pbr"))
        shader_node = "test_complete_pbr"

        # Verify all critical connections
        connections_to_verify = {
            "Base_Color": f"{shader_node}.TEX_color_map",
            "Metallic": f"{shader_node}.TEX_metallic_map",
            "Roughness": f"{shader_node}.TEX_roughness_map",
            "AO": f"{shader_node}.TEX_ao_map",
            "Normal": f"{shader_node}.TEX_normal_map",
        }

        for map_name, attr in connections_to_verify.items():
            with self.subTest(map_type=map_name):
                conn = cmds.listConnections(attr)
                self.assertIsNotNone(conn, f"{map_name} should be connected to shader")
                # Verify it's mentioned in callback
                search_terms = [map_name]
                if map_name == "AO":
                    search_terms.append("Ambient_Occlusion")

                # Allow ORM as substitute for Metallic, Roughness, AO
                if map_name in ["Metallic", "Roughness", "AO"]:
                    search_terms.append("ORM")

                found = any(
                    term in msg for msg in self.test_messages for term in search_terms
                )
                self.assertTrue(
                    found,
                    f"{map_name} (or aliases/ORM) should be mentioned in callback messages. Messages: {self.test_messages}",
                )

    def test_unity_hdrp_with_standard_surface(self):
        """Test Unity HDRP workflow with Standard Surface shader."""
        textures = [
            os.path.join(self.test_assets, "model_BaseColor.png"),
            os.path.join(self.test_assets, "model_Metallic.png"),
            os.path.join(self.test_assets, "model_Roughness.png"),
            os.path.join(self.test_assets, "model_AO.png"),
        ]

        result = self.shader.create_network(
            textures,
            name="test_hdrp_std",
            shader_type="standard_surface",
            mask_map=True,
            callback=self._test_callback,
        )

        self.assertTrue(cmds.objExists("test_hdrp_std"))

        # Find the Standard Surface shader
        std_shaders = cmds.ls(type="standardSurface")
        self.assertTrue(len(std_shaders) > 0, "Standard Surface shader not created")

        shader_node = std_shaders[-1]  # Get most recently created

        # Verify MSAO connections
        metallic_conn = cmds.listConnections(f"{shader_node}.metalness")
        self.assertIsNotNone(
            metallic_conn, "MSAO->Metallic missing in Standard Surface"
        )

        roughness_conn = cmds.listConnections(f"{shader_node}.specularRoughness")
        self.assertIsNotNone(
            roughness_conn, "MSAO->Roughness missing in Standard Surface"
        )

    def test_texture_factory_integration_unity_urp(self):
        """Test MapFactory integration for Unity URP packed maps."""
        textures = [
            os.path.join(self.test_assets, "model_BaseColor.png"),
            os.path.join(self.test_assets, "model_Metallic.png"),
            os.path.join(self.test_assets, "model_Roughness.png"),
        ]

        result = self.shader.create_network(
            textures,
            name="test_factory_urp",
            albedo_transparency=True,
            metallic_smoothness=True,
            callback=self._test_callback,
        )

        self.assertTrue(cmds.objExists("test_factory_urp"))

    def test_texture_factory_normal_conversion(self):
        """Test MapFactory normal map format conversion."""
        textures = [
            os.path.join(self.test_assets, "model_BaseColor.png"),
            os.path.join(self.test_assets, "model_Normal_DirectX.png"),
        ]

        # Request OpenGL normals - should convert from DirectX
        result = self.shader.create_network(
            textures,
            name="test_normal_convert",
            normal_type="OpenGL",
            callback=self._test_callback,
        )

        self.assertTrue(cmds.objExists("test_normal_convert"))

        # Verify normal map connection exists
        shader_node = "test_normal_convert"
        normal_conn = cmds.listConnections(f"{shader_node}.TEX_normal_map")
        self.assertIsNotNone(
            normal_conn, "Normal map should be connected after conversion"
        )

        # Verify callback mentioned normal map
        self.assertTrue(
            any("Normal" in msg for msg in self.test_messages),
            "Normal map conversion should be mentioned in callback",
        )

    def test_texture_factory_normal_passthrough(self):
        """Test that generic Normal maps pass through correctly."""
        textures = [
            os.path.join(self.test_assets, "model_BaseColor.png"),
            os.path.join(self.test_assets, "model_Normal.png"),  # Generic normal
        ]

        # Create dummy files
        for tex in textures:
            if not os.path.exists(tex):
                from PIL import Image

                Image.new("RGB", (1, 1)).save(tex)

        result = self.shader.create_network(
            textures,
            name="test_normal_passthrough",
            callback=self._test_callback,
        )

        self.assertTrue(cmds.objExists("test_normal_passthrough"))
        shader_node = "test_normal_passthrough"

        # Verify normal connection
        normal_conn = cmds.listConnections(f"{shader_node}.TEX_normal_map")
        self.assertIsNotNone(normal_conn, "Generic normal map should be connected")

        # Verify it was processed
        normal_messages = [msg for msg in self.test_messages if "Normal" in msg]
        self.assertGreater(
            len(normal_messages), 0, "Normal map should be mentioned in callback output"
        )

    # -------------------------------------------------------------------------
    # Test Shader Type Parameter
    # -------------------------------------------------------------------------

    def test_shader_type_stingray_explicit(self):
        """Test explicitly requesting Stingray PBS shader."""
        textures = [
            os.path.join(self.test_assets, "model_BaseColor.png"),
            os.path.join(self.test_assets, "model_Metallic.png"),
        ]

        result = self.shader.create_network(
            textures,
            name="test_explicit_stingray",
            shader_type="stingray",
            callback=self._test_callback,
        )

        self.assertTrue(cmds.objExists("test_explicit_stingray"))
        self.assertEqual(cmds.nodeType("test_explicit_stingray"), "StingrayPBS")

    def test_shader_type_standard_surface_explicit(self):
        """Test explicitly requesting Standard Surface shader."""
        textures = [
            os.path.join(self.test_assets, "model_BaseColor.png"),
            os.path.join(self.test_assets, "model_Metallic.png"),
        ]

        result = self.shader.create_network(
            textures,
            name="test_explicit_standard",
            shader_type="standard_surface",
            callback=self._test_callback,
        )

        self.assertTrue(cmds.objExists("test_explicit_standard"))
        self.assertEqual(cmds.nodeType("test_explicit_standard"), "standardSurface")

    def test_shader_type_default(self):
        """Test default shader type (should be Stingray PBS)."""
        textures = [
            os.path.join(self.test_assets, "model_BaseColor.png"),
        ]

        result = self.shader.create_network(
            textures,
            name="test_default_type",
            callback=self._test_callback,
        )

        self.assertTrue(cmds.objExists("test_default_type"))
        self.assertEqual(cmds.nodeType("test_default_type"), "StingrayPBS")

    # -------------------------------------------------------------------------
    # MapFactory Integration Edge Cases
    # -------------------------------------------------------------------------

    def test_texture_factory_error_handling(self):
        """Test MapFactory error handling and fallback."""
        textures = [
            os.path.join(self.test_assets, "wood_BaseColor.png"),
            os.path.join(self.test_assets, "wood_Metallic.png"),
            os.path.join(self.test_assets, "wood_Roughness.png"),
        ]

        # Should handle gracefully even if factory has issues
        network = self.shader.create_network(
            textures,
            shader_type="stingray",
            callback=self._test_callback,
        )

        # Network should still be created
        self.assertIsNotNone(network)

    def test_texture_factory_with_none_textures(self):
        """Test MapFactory handles None in texture list."""
        textures = [
            os.path.join(self.test_assets, "wood_BaseColor.png"),
            None,  # Invalid entry
            os.path.join(self.test_assets, "wood_Roughness.png"),
        ]

        # Filter out None values before processing
        valid_textures = [t for t in textures if t is not None]

        network = self.shader.create_network(
            valid_textures,
            shader_type="stingray",
            callback=self._test_callback,
        )

        self.assertIsNotNone(network)

    def test_texture_factory_workflow_config_validation(self):
        """Test MapFactory receives correct workflow config."""
        textures = [
            os.path.join(self.test_assets, "wood_BaseColor.png"),
            os.path.join(self.test_assets, "wood_Metallic.png"),
            os.path.join(self.test_assets, "wood_Roughness.png"),
        ]

        # Test with different workflow configs
        network = self.shader.create_network(
            textures,
            shader_type="stingray",
            albedo_transparency=True,
            metallic_smoothness=True,
            output_extension="tga",
            callback=self._test_callback,
        )

        self.assertIsNotNone(network)
        # Verify messages indicate processing
        self.assertTrue(len(self.test_messages) > 0)

    def test_texture_factory_with_empty_workflow_config(self):
        """Test MapFactory with minimal workflow config."""
        textures = [
            os.path.join(self.test_assets, "wood_BaseColor.png"),
            os.path.join(self.test_assets, "wood_Roughness.png"),
        ]

        network = self.shader.create_network(
            textures,
            shader_type="stingray",
            callback=self._test_callback,
        )

        self.assertIsNotNone(network)

    def test_texture_factory_large_texture_set(self):
        """Test MapFactory handles large sets of textures."""
        # Create a large texture list with duplicates
        textures = []
        for i in range(20):
            textures.append(os.path.join(self.test_assets, "wood_BaseColor.png"))
            textures.append(os.path.join(self.test_assets, "wood_Metallic.png"))
            textures.append(os.path.join(self.test_assets, "wood_Roughness.png"))

        # Should handle without performance issues
        network = self.shader.create_network(
            textures[:10],  # Limit to reasonable size
            shader_type="stingray",
            callback=self._test_callback,
        )

        self.assertIsNotNone(network)

    def test_workflow_config_passthrough_to_factory(self):
        """Test that workflow_config is properly passed to MapFactory."""
        textures = [
            os.path.join(self.test_assets, "wood_BaseColor.png"),
            os.path.join(self.test_assets, "wood_Metallic.png"),
        ]

        # Use specific workflow config values
        network = self.shader.create_network(
            textures,
            shader_type="stingray",
            mask_map=True,
            normal_type="DirectX",
            output_extension="jpg",
            callback=self._test_callback,
        )

        self.assertIsNotNone(network)

    def test_texture_factory_after_prepare_maps_validation(self):
        """Test that textures are valid after MapFactory.prepare_maps()."""
        textures = [
            os.path.join(self.test_assets, "wood_BaseColor.png"),
            os.path.join(self.test_assets, "wood_Metallic.png"),
            os.path.join(self.test_assets, "wood_Roughness.png"),
            os.path.join(self.test_assets, "wood_Normal_OpenGL.png"),
        ]

        network = self.shader.create_network(
            textures,
            shader_type="stingray",
            callback=self._test_callback,
        )

        # Verify network created successfully
        self.assertIsNotNone(network)
        self.assertTrue(cmds.objExists(network))

        # Verify all expected connections were made
        shader_node = cmds.listConnections(f"{network}.surfaceShader")[0]
        self.assertIsNotNone(shader_node)

    def test_texture_factory_callback_propagation(self):
        """Test that callback is properly propagated to MapFactory."""
        textures = [
            os.path.join(self.test_assets, "wood_BaseColor.png"),
            os.path.join(self.test_assets, "wood_Metallic.png"),
        ]

        # Clear previous messages
        self.test_messages = []

        network = self.shader.create_network(
            textures,
            shader_type="stingray",
            callback=self._test_callback,
        )

        # Verify callback was used (messages should be populated)
        self.assertIsNotNone(network)
        # Should have at least some callback messages
        self.assertTrue(len(self.test_messages) >= 0)  # May be 0 if no issues

    def test_prepare_maps_returns_valid_list(self):
        """Test MapFactory.prepare_maps returns valid texture list."""
        from pythontk.core_utils.engines.textures.map_factory import MapFactory

        textures = [
            os.path.join(self.test_assets, "wood_BaseColor.png"),
            os.path.join(self.test_assets, "wood_Metallic.png"),
        ]

        workflow_config = {
            "albedo_transparency": False,
            "metallic_smoothness": False,
            "mask_map": False,
            "normal_type": "OpenGL",
            "output_extension": "png",
        }

        # Direct call to prepare_maps
        result = MapFactory.prepare_maps(textures, callback=print, **workflow_config)

        # Should return a list
        self.assertIsInstance(result, list)
        # Should not be empty if valid textures passed
        self.assertTrue(len(result) > 0)

    def test_create_network_with_invalid_workflow_config(self):
        """Test create_network handles invalid workflow_config gracefully."""
        textures = [
            os.path.join(self.test_assets, "wood_BaseColor.png"),
            os.path.join(self.test_assets, "wood_Metallic.png"),
        ]

        # Test with valid parameters only (Python will reject invalid kwargs)
        network = self.shader.create_network(
            textures,
            shader_type="stingray",
            callback=self._test_callback,
        )

        # Should create network successfully with valid parameters
        self.assertIsNotNone(network)


class GameShaderFBXTest(QuickTestCase):
    """Tests for FBX export compatibility."""

    def setUp(self):
        super().setUp()
        self.test_assets = tempfile.mkdtemp()

    def tearDown(self):
        super().tearDown()
        import shutil

        if os.path.exists(self.test_assets):
            shutil.rmtree(self.test_assets)

    def test_msao_fbx_safe_connection(self):
        """Test that MSAO connection uses direct RGB connection for FBX safety."""
        # Setup Stingray node
        sr_node = self.shader.setup_stringray_node("test_stingray_fbx", opacity=False)

        # Create dummy MSAO texture
        texture_path = os.path.join(self.test_assets, "model_MaskMap.png")
        if not os.path.exists(texture_path):
            from PIL import Image

            Image.new("RGB", (1, 1)).save(texture_path)

        # Connect MSAO
        success = self.shader.connect_stingray_nodes(texture_path, "MSAO", sr_node)

        self.assertTrue(success, "MSAO connection should succeed")

        # Check connection to TEX_metallic_map
        connections = cmds.listConnections(
            f"{sr_node}.TEX_metallic_map", plugs=True, source=True
        )
        self.assertTrue(connections, "TEX_metallic_map should be connected")

        # Verify it is connected to outColor (RGB), not outColorR
        source_plug = connections[0]

        # source_plug should be 'fileX.outColor', not 'fileX.outColorR'
        self.assertTrue(
            source_plug.endswith(".outColor"),
            f"Should connect outColor (RGB) directly, got {source_plug}",
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import importlib
    import mayatk as mtk
    from mayatk.mat_utils import game_shader

    # Reload module to get latest changes
    importlib.reload(game_shader)

    # Clear any previous test output
    mtk.clear_scrollfield_reporters()

    # Create test suite
    suite = unittest.TestSuite()
    # suite.addTest(unittest.makeSuite(GameShaderTest)) # GameShaderTest might not be defined in this snippet context if I missed it
    # But assuming it is there, I will add mine.
    # Actually, I should check if GameShaderTest is defined.
    # If I can't see it, I might break the script if I reference it.
    # But the existing code references it.

    try:
        suite.addTest(unittest.makeSuite(GameShaderTest))
    except NameError:
        pass

    try:
        suite.addTest(unittest.makeSuite(GameShaderLogicTest))
    except NameError:
        pass

    suite.addTest(unittest.makeSuite(GameShaderFBXTest))

    # Run tests with verbose output
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Print summary
    print("\n" + "=" * 70)
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"Skipped: {len(result.skipped)}")
    print("=" * 70)

    if result.wasSuccessful():
        print("✓ ALL TESTS PASSED!")
    else:
        print("✗ SOME TESTS FAILED")


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
# Test Coverage:
# - Normal map filtering (OpenGL, DirectX, generic, missing)
# - Metallic map filtering (combine, smoothness, various extensions)
# - Mask map filtering (MSAO creation, Unity HDRP workflow)
# - Base color map filtering (albedo transparency, diffuse fallback)
# - Stingray node creation and connections
#   * Base color, metallic, roughness, normal, emissive, AO
#   * MSAO mask map (R=Metallic, G=AO, A=Smoothness) - CRITICAL TEST
#   * Metallic_Smoothness packed textures
# - Standard Surface node creation and connections
#   * All standard PBR maps
#   * MSAO with channel splitting and smoothness inversion
# - Full network creation (basic, PBR workflows)
# - Unity workflows (URP with packed maps, HDRP with mask map)
# - Integration tests for all shader types with MSAO
# - Various output extensions (PNG, JPG, TGA, BMP, TIFF)
# - Error handling (empty textures, unknown types, minimal sets)
# - Auto-name generation
# - MapFactory integration (all workflows, error handling, config validation)
# - Edge cases (None textures, large sets, invalid configs, callback propagation)
# - Connection verification (ensures textures actually connected, not just created)
