# !/usr/bin/python
# coding=utf-8
"""Test Suite for mat_utils.shader_attribute_map.

The mapping itself is a pure-Python data module; ``connect_channel`` (and the
plug mechanics behind it) is the one Maya-touching part, covered by
:class:`TestConnectChannel` below.
"""

import math
import unittest

import maya.cmds as cmds

from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap, ShaderAttrs

from base_test import MayaTkTestCase, QuickTestCase


class TestLogicalChannels(QuickTestCase):
    def test_returns_the_core_channels_then_the_openpbr_lobes(self):
        from mayatk.mat_utils.shader_attribute_map import LOBE_CHANNELS

        channels = ShaderAttributeMap.logical_channels()
        self.assertEqual(
            channels[:8],
            (
                "baseColor",
                "emission",
                "specular",
                "roughness",
                "metallic",
                "opacity",
                "normal",
                "ambientOcclusion",
            ),
        )
        self.assertEqual(channels[8:], LOBE_CHANNELS)

    def test_the_lobes_are_the_shared_vocabulary(self):
        """Every lobe channel is one pythontk names, so a map type wires the same
        input here as on blendertk's Principled table."""
        import pythontk as ptk

        from mayatk.mat_utils.shader_attribute_map import LOBE_CHANNELS

        shared = set(ptk.MapRegistry.LOBE_TYPE_CHANNELS.values()) - {"specular"}
        self.assertEqual(set(LOBE_CHANNELS), shared)

    def test_openpbr_declares_every_lobe(self):
        from mayatk.mat_utils.shader_attribute_map import LOBE_CHANNELS

        for channel in LOBE_CHANNELS:
            self.assertIsNotNone(
                ShaderAttributeMap.get_attr("openPBRSurface", channel), channel
            )

    def test_the_classic_shaders_declare_no_lobe(self):
        from mayatk.mat_utils.shader_attribute_map import LOBE_CHANNELS

        for shader_type in ("lambert", "blinn", "phong", "StingrayPBS"):
            for channel in LOBE_CHANNELS:
                self.assertIsNone(
                    ShaderAttributeMap.get_attr(shader_type, channel),
                    f"{shader_type}.{channel}",
                )


class TestGetAttr(QuickTestCase):
    def test_returns_known_lambert_baseColor(self):
        self.assertEqual(
            ShaderAttributeMap.get_attr("lambert", "baseColor"),
            ("color", "outColor"),
        )

    def test_returns_none_for_unsupported_logical(self):
        # lambert has no metallic.
        self.assertIsNone(ShaderAttributeMap.get_attr("lambert", "metallic"))

    def test_returns_none_for_unknown_shader_type(self):
        self.assertIsNone(
            ShaderAttributeMap.get_attr("nonexistent_shader", "baseColor")
        )

    def test_returns_none_for_invalid_logical_channel(self):
        self.assertIsNone(ShaderAttributeMap.get_attr("lambert", "made_up_channel"))

    def test_stingray_uses_TEX_prefix(self):
        self.assertEqual(
            ShaderAttributeMap.get_attr("StingrayPBS", "baseColor"),
            ("TEX_color_map", "outColor"),
        )


class TestGetMapping(QuickTestCase):
    def test_returns_empty_for_unknown_src(self):
        self.assertEqual(
            ShaderAttributeMap.get_mapping("bogus", "lambert"),
            tuple(),
        )

    def test_returns_empty_for_unknown_dst(self):
        self.assertEqual(
            ShaderAttributeMap.get_mapping("lambert", "bogus"),
            tuple(),
        )

    def test_lambert_to_stingray_only_includes_shared_channels(self):
        # lambert has baseColor, emission, opacity (no specular/roughness/metallic/normal/AO).
        # StingrayPBS has all of those — intersection = baseColor, emission, opacity.
        pairs = ShaderAttributeMap.get_mapping("lambert", "StingrayPBS")
        src_attrs = {p[0] for p in pairs}
        self.assertEqual(src_attrs, {"color", "incandescence", "transparency"})

    def test_pair_structure_is_src_attr_src_plug_dst_attr(self):
        pairs = ShaderAttributeMap.get_mapping("aiStandardSurface", "standardSurface")
        for p in pairs:
            self.assertEqual(len(p), 3)
            for component in p:
                self.assertIsInstance(component, str)


class TestUpdateAttr(QuickTestCase):
    def setUp(self):
        # Snapshot the current state so we can restore — these are class-level
        # mutations and would leak across tests otherwise.
        self._saved = ShaderAttributeMap.SHADER_ATTRS["lambert"]

    def tearDown(self):
        ShaderAttributeMap.SHADER_ATTRS["lambert"] = self._saved

    def test_update_replaces_attr(self):
        ShaderAttributeMap.update_attr(
            "lambert", "baseColor", ("custom_attr", "custom_plug")
        )
        self.assertEqual(
            ShaderAttributeMap.get_attr("lambert", "baseColor"),
            ("custom_attr", "custom_plug"),
        )

    def test_update_to_none_clears(self):
        ShaderAttributeMap.update_attr("lambert", "baseColor", None)
        self.assertIsNone(ShaderAttributeMap.get_attr("lambert", "baseColor"))

    def test_update_ignored_for_unknown_shader(self):
        # Silent no-op (no exception) when the shader isn't registered.
        ShaderAttributeMap.update_attr("nonexistent", "baseColor", ("a", "b"))
        self.assertNotIn("nonexistent", ShaderAttributeMap.SHADER_ATTRS)


class TestAddShaderType(QuickTestCase):
    def tearDown(self):
        ShaderAttributeMap.SHADER_ATTRS.pop("__test_shader__", None)

    def test_add_then_get_attr_works(self):
        attrs = ShaderAttrs(
            baseColor=("my_base", "outColor"),
            emission=None,
            specular=None,
            roughness=None,
            metallic=None,
            opacity=None,
            normal=None,
            ambientOcclusion=None,
        )
        ShaderAttributeMap.add_shader_type("__test_shader__", attrs)
        self.assertEqual(
            ShaderAttributeMap.get_attr("__test_shader__", "baseColor"),
            ("my_base", "outColor"),
        )


class TestAsDict(QuickTestCase):
    def test_returns_dict_of_dicts_with_all_logical_channels(self):
        d = ShaderAttributeMap.as_dict()
        self.assertIn("lambert", d)
        self.assertIn("StingrayPBS", d)
        # Each inner dict must contain every logical channel key.
        for shader_type, attrs_dict in d.items():
            self.assertEqual(
                set(attrs_dict.keys()),
                set(ShaderAttributeMap.logical_channels()),
                f"{shader_type} missing channels",
            )


class TestConstantAttr(QuickTestCase):
    """constant_attr -- which attribute holds an UNMAPPED channel's value."""

    def test_a_stingray_value_lives_on_its_uniform_not_its_sampler(self):
        """An undriven ``TEX_*`` sampler reads (0, 0, 0): a Stingray's value is
        the graph's uniform."""
        for logical, attr in (
            ("baseColor", "base_color"),
            ("emission", "emissive"),
            ("roughness", "roughness"),
            ("metallic", "metallic"),
            ("opacity", "opacity"),
        ):
            with self.subTest(logical):
                self.assertEqual(
                    ShaderAttributeMap.constant_attr("StingrayPBS", logical), attr
                )

    def test_a_sampler_with_no_uniform_holds_no_value(self):
        self.assertIsNone(
            ShaderAttributeMap.constant_attr("StingrayPBS", "ambientOcclusion")
        )

    def test_a_normal_is_never_a_value(self):
        """An undriven normal input means the surface's own normal; its literal
        (``normalCamera`` (1, 1, 1), ``geometryCoatNormal`` (0, 0, 0)) is no
        tangent-space value -- the coat normal included."""
        for shader_type in ShaderAttributeMap.SHADER_ATTRS:
            for logical in ("normal", "coatNormal"):
                with self.subTest(shader_type=shader_type, logical=logical):
                    self.assertIsNone(
                        ShaderAttributeMap.constant_attr(shader_type, logical)
                    )

    def test_every_other_type_holds_it_on_the_declared_slot(self):
        self.assertEqual(
            ShaderAttributeMap.constant_attr("lambert", "opacity"), "transparency"
        )
        self.assertEqual(
            ShaderAttributeMap.constant_attr("standardSurface", "roughness"),
            "specularRoughness",
        )
        self.assertIsNone(ShaderAttributeMap.constant_attr("lambert", "metallic"))
        self.assertIsNone(ShaderAttributeMap.constant_attr("bogus", "baseColor"))


class TestReadConstant(MayaTkTestCase):
    """read_constant -- an unmapped channel's value, in the channel's terms."""

    def _shader(self, node_type):
        """A *node_type* shader, or a skip where this session lacks the type.

        A plug-in type whose plug-in is not loaded comes back as an
        ``unknown`` placeholder node rather than an error (probed in mayapy:
        ``aiStandardSurface`` before ``mtoa`` loads), so the TYPE is checked.
        """
        if node_type.startswith("ai"):
            try:
                cmds.loadPlugin("mtoa", quiet=True)
            except RuntimeError:
                pass  # no Arnold here: the type check below skips
        try:
            node = cmds.shadingNode(node_type, asShader=True)
        except RuntimeError as error:
            self.skipTest(f"{node_type} unavailable: {error}")
        if cmds.nodeType(node) != node_type:
            cmds.delete(node)
            self.skipTest(f"{node_type} unavailable in this session")
        return node

    def test_a_phong_roughness_reads_as_a_roughness(self):
        """phong's roughness slot is ``cosinePower``, a specular EXPONENT
        (default 20): read raw, a phong source wrote 20 into a 0..1 roughness
        -- fully rough. It reads as the Beckmann width the exponent stands
        for, the inverse of the Maya bridge's ``_roughness_to_cosine_power``."""
        phong = self._shader("phong")
        (default,) = ShaderAttributeMap.read_constant(phong, "roughness")
        self.assertAlmostEqual(default, math.sqrt(2.0 / 22.0), places=5)
        cmds.setAttr(f"{phong}.cosinePower", 2.0)  # the broadest highlight
        (broad,) = ShaderAttributeMap.read_constant(phong, "roughness")
        cmds.setAttr(f"{phong}.cosinePower", 100.0)  # the tightest
        (tight,) = ShaderAttributeMap.read_constant(phong, "roughness")
        self.assertTrue(0.0 < tight < default < broad <= 1.0, (tight, default, broad))

    def test_an_unweighted_emission_reads_black(self):
        """The PBR trio keeps emission behind a separate scalar that defaults
        to 0 while ``emissionColor`` defaults to WHITE: read alone, every
        untouched material read as a white emitter."""
        for node_type in ("standardSurface", "aiStandardSurface", "openPBRSurface"):
            with self.subTest(node_type):
                shader = self._shader(node_type)
                self.assertEqual(
                    ShaderAttributeMap.read_constant(shader, "emission"),
                    (0.0, 0.0, 0.0),
                )

    def test_a_weight_scales_the_colour(self):
        ss = self._shader("standardSurface")
        cmds.setAttr(f"{ss}.emission", 0.5)
        cmds.setAttr(f"{ss}.emissionColor", 1.0, 0.5, 0.0, type="double3")
        for got, want in zip(
            ShaderAttributeMap.read_constant(ss, "emission"), (0.5, 0.25, 0.0)
        ):
            self.assertAlmostEqual(got, want, places=5)

    def test_a_luminance_gate_opens_the_colour_without_scaling_it(self):
        """openPBR's luminance is in nits, not a 0-1 scale: it gates."""
        opbr = self._shader("openPBRSurface")
        cmds.setAttr(f"{opbr}.emissionLuminance", 1000.0)
        cmds.setAttr(f"{opbr}.emissionColor", 1.0, 0.5, 0.0, type="double3")
        for got, want in zip(
            ShaderAttributeMap.read_constant(opbr, "emission"), (1.0, 0.5, 0.0)
        ):
            self.assertAlmostEqual(got, want, places=5)

    def test_emission_weight_reads_only_the_types_own_scalar(self):
        """An explicit table, never a guessed attribute name: a type it does not
        list is ungated (1.0)."""
        ss = self._shader("standardSurface")
        self.assertEqual(ShaderAttributeMap.emission_weight(ss), 0.0)
        cmds.setAttr(f"{ss}.emission", 0.25)
        self.assertAlmostEqual(ShaderAttributeMap.emission_weight(ss), 0.25)
        self.assertEqual(
            ShaderAttributeMap.emission_weight(self._shader("lambert")), 1.0
        )

    def test_emission_weight_reads_a_stingray_emissive_intensity(self):
        """StingrayPBS's graph multiplies its emissive by ``emissive_intensity``;
        unread, a dimmed fixture shipped at full glow (2026-10-16: moody-room
        ceiling panels lit WebXR at 1.0 where the scene asked for less)."""
        try:
            cmds.loadPlugin("shaderFXPlugin", quiet=True)
        except RuntimeError:
            self.skipTest("shaderFXPlugin not available")
        from mayatk.mat_utils._mat_utils import MatUtils

        mat = MatUtils.create_stingray_shader("glowSrp")
        if not cmds.attributeQuery("emissive_intensity", node=mat, exists=True):
            self.skipTest("this StingrayPBS graph has no emissive_intensity")
        cmds.setAttr(f"{mat}.emissive_intensity", 0.3)
        self.assertAlmostEqual(ShaderAttributeMap.emission_weight(mat), 0.3, places=5)


class TestConnectChannel(MayaTkTestCase):
    """connect_channel — apply a declaration against a REAL attribute.

    The declaration and the live attribute do not always share an arity: every
    PBR shader here declares ``opacity`` as ``outAlpha`` (opacity IS the alpha)
    while the attribute is a float3. Making the types agree by swapping the
    SOURCE plug changes which data flows — that is how a texture's color ended
    up driving transparency — so a scalar drives every CHILD instead.
    """

    def setUp(self):
        super().setUp()
        self.shader = cmds.shadingNode("standardSurface", asShader=True, name="cc_ss")
        self.file_node = cmds.shadingNode("file", asTexture=True, name="cc_file")

    def _sources(self, attr):
        return (
            cmds.listConnections(
                f"{self.shader}.{attr}", source=True, destination=False, plugs=True
            )
            or []
        )

    def test_scalar_source_broadcasts_across_a_compound(self):
        self.assertTrue(
            ShaderAttributeMap.connect_channel(self.file_node, "opacity", self.shader)
        )
        for child in ("opacityR", "opacityG", "opacityB"):
            self.assertEqual(
                [p.split(".")[-1] for p in self._sources(child)],
                ["outAlpha"],
                f"{child} not driven by the declared plug",
            )

    def test_scalar_source_into_a_scalar_attr_stays_direct(self):
        self.assertTrue(
            ShaderAttributeMap.connect_channel(self.file_node, "roughness", self.shader)
        )
        self.assertEqual(
            [p.split(".")[-1] for p in self._sources("specularRoughness")],
            ["outColorR"],
        )

    def _packed_map(self, grey, alpha):
        """Point the file node at a real RGBA PNG: *grey* in R/G/B, *alpha* in A."""
        import numpy as np
        import pythontk as ptk
        from PIL import Image

        store = ptk.TempArtifacts("cc_packed_map", policy="scoped")
        self.addCleanup(store.cleanup)
        path = store.path(extension=".png").replace("\\", "/")
        Image.fromarray(np.full((4, 4, 4), (grey, grey, grey, alpha), np.uint8)).save(
            path
        )
        cmds.setAttr(f"{self.file_node}.fileTextureName", path, type="string")

    def test_pbr_scalar_channels_read_the_colour_not_a_packed_alpha(self):
        """A packed map keeps its scalar in RGB and something ELSE in alpha:
        Unity's MetallicSmoothness carries smoothness there. Read off
        ``outAlpha``, a non-metal wearing that map came out ~78% metal -- a
        production transfer rendered at a third of its source's brightness,
        legs black. RGB is where a grey map carries its value too."""
        self._packed_map(grey=0, alpha=200)
        for logical, attr in (
            ("metallic", "metalness"),
            ("roughness", "specularRoughness"),
        ):
            with self.subTest(logical):
                self.assertTrue(
                    ShaderAttributeMap.connect_channel(
                        self.file_node, logical, self.shader
                    )
                )
                self.assertAlmostEqual(
                    self.sample_input(f"{self.shader}.{attr}"), 0.0, places=3
                )

    def test_openpbr_normal_lands_on_the_slot_this_maya_has(self):
        """The OpenPBR spec names the input ``geometryNormal``; Maya 2025's
        openPBRSurface exposes the classic ``normalCamera`` instead, so the
        declared slot alone missed and every replayed normal map was dropped
        (GameShader already probes both)."""
        try:
            shader = cmds.shadingNode("openPBRSurface", asShader=True)
        except RuntimeError as error:
            self.skipTest(f"openPBRSurface unavailable: {error}")
        if cmds.nodeType(shader) != "openPBRSurface":  # an `unknown` placeholder
            self.skipTest("openPBRSurface unavailable in this session")
        self.assertTrue(
            ShaderAttributeMap.connect_channel(self.file_node, "normal", shader)
        )
        slot = next(
            a
            for a in ("geometryNormal", "normalCamera")
            if cmds.attributeQuery(a, node=shader, exists=True)
        )
        self.assertTrue(
            cmds.listConnections(f"{shader}.{slot}", source=True, destination=False)
        )

    def test_compound_source_into_a_compound_attr_stays_direct(self):
        self.assertTrue(
            ShaderAttributeMap.connect_channel(self.file_node, "baseColor", self.shader)
        )
        self.assertEqual(
            [p.split(".")[-1] for p in self._sources("baseColor")], ["outColor"]
        )

    def _stingray(self, opacity=False):
        """A StingrayPBS carrying a real ShaderFX graph.

        A bare ``shadingNode("StingrayPBS")`` has NO graph loaded in batch mode
        and therefore none of the TEX_* slots -- building one through the engine
        (which loads Standard.sfx / Standard_Transparent.sfx) is the only way to
        test against the attributes a production node actually has.
        """
        from mayatk.mat_utils.game_shader import GameShader

        try:
            return str(
                GameShader(log_level="WARNING").setup_stringray_node(
                    f"cc_sr_{'t' if opacity else 's'}", opacity
                )
            )
        except RuntimeError as error:  # no shaderFX plugin on this install
            self.skipTest(f"StingrayPBS unavailable: {error}")

    def test_stingray_channel_enables_its_use_toggle(self):
        """A StingrayPBS map slot is INERT until its ``use_*_map`` toggle is on.

        The manifest-replay path connected the file and stopped, so a rescued
        texture was wired and invisible -- indistinguishable from "the rescue
        didn't run" while debugging. The classified path (GameShader._wire) has
        always set the toggle; this is the same rule applied at the shared
        connector so both routes agree.
        """
        shader = self._stingray()
        self.assertTrue(
            ShaderAttributeMap.connect_channel(self.file_node, "baseColor", shader)
        )
        self.assertEqual(cmds.getAttr(f"{shader}.use_color_map"), 1)

    def test_stingray_opacity_is_not_written_into_the_transparent_uniform(self):
        """The transparent graph's ``opacity`` is a UNIFORM, not a sampler.

        A texture wired there is one flat value (verified live: renders with
        the map connected and disconnected are byte-identical), and the
        ``use_opacity_map`` selector routes past it anyway. The declaration
        stays readable for conversion, but the connector must refuse to write
        it -- the per-pixel route on that graph is the colour map's alpha.
        """
        shader = self._stingray(opacity=True)
        self.assertFalse(
            ShaderAttributeMap.connect_channel(self.file_node, "opacity", shader)
        )
        self.assertIsNone(
            cmds.listConnections(f"{shader}.opacity", source=True, destination=False)
        )

    def test_map_toggle_names_cover_both_shaderfx_shapes(self):
        """The scalar slot is the trap: a bare "TEX_" -> "use_" substitution
        leaves ``opacity`` pointing at ITSELF, so the toggle never gets set and
        the map stays inert. Both routes into a StingrayPBS read this rule."""
        self.assertEqual(
            ShaderAttributeMap.map_toggle_attr("TEX_color_map"), "use_color_map"
        )
        self.assertEqual(
            ShaderAttributeMap.map_toggle_attr("opacity"), "use_opacity_map"
        )

    def test_mask_map_toggle_selects_the_mask_map(self):
        """``use_opacity_map`` is a SOURCE SELECTOR on the ShaderFX graphs, not
        an enable: 1 reads the colour map's alpha, 0 the mask map's red channel.
        The shared rule has to carry the value, or every route that "enables"
        a just-connected ``TEX_mask_map`` points the graph away from it."""
        self.assertEqual(
            ShaderAttributeMap.map_toggle_state("TEX_mask_map"),
            ("use_opacity_map", 0),
        )
        self.assertEqual(
            ShaderAttributeMap.map_toggle_state("TEX_color_map"),
            ("use_color_map", 1),
        )

    def test_stingray_opacity_wires_into_the_masked_graph_compound(self):
        """Masked graph: the opacity texture binds to ``TEX_mask_map`` through
        the COMPOUND plug (a per-child bind is an unbound sampler that discards
        every fragment -- verified live), and the selector is left at 0."""
        from mayatk.mat_utils._mat_utils import MatUtils

        shader = MatUtils.create_stingray_shader(
            "test_masked_replay", opacity=True, opacity_mode="masked"
        )
        self.assertTrue(
            ShaderAttributeMap.connect_channel(self.file_node, "opacity", shader)
        )
        parent = cmds.listConnections(
            f"{shader}.TEX_mask_map", source=True, destination=False, plugs=True
        )
        self.assertEqual([p.split(".")[-1] for p in parent or []], ["outColor"])
        self.assertIsNone(
            cmds.listConnections(
                f"{shader}.TEX_mask_mapX", source=True, destination=False
            )
        )
        self.assertEqual(cmds.getAttr(f"{shader}.use_opacity_map"), 0)

    def test_stingray_opacity_targets_a_slot_that_exists(self):
        """Verified live: neither StingrayPBS graph exposes ``TEX_opacity_map``.

        The transparent graph carries a scalar ``opacity`` plus
        ``use_opacity_map``; the map declared the non-existent TEX_ slot, so
        every Stingray opacity rescue silently no-oped.
        """
        attr, plug = ShaderAttributeMap.get_attr("StingrayPBS", "opacity")
        self.assertEqual((attr, plug), ("opacity", "outAlpha"))

    def test_unmapped_channel_returns_false(self):
        """standardSurface declares no ambientOcclusion slot."""
        self.assertFalse(
            ShaderAttributeMap.connect_channel(
                self.file_node, "ambientOcclusion", self.shader
            )
        )

    def test_unknown_shader_type_returns_false(self):
        self.assertFalse(
            ShaderAttributeMap.connect_channel(
                self.file_node, "opacity", self.shader, shader_type="notAShader"
            )
        )

    def test_missing_attribute_returns_false(self):
        """A StingrayPBS graph need not expose every declared slot.

        Measured against a REAL non-transparent graph: ``Standard.sfx`` carries
        no opacity slot at all (``Standard_Transparent.sfx`` is the one that
        does), so the declaration has nothing to bind to and must decline
        rather than raise.
        """
        self.assertFalse(
            ShaderAttributeMap.connect_channel(
                self.file_node, "opacity", self._stingray()
            )
        )

    def test_partial_broadcast_is_rolled_back(self):
        """A half-driven compound is worse than an unconnected one."""
        cmds.setAttr(f"{self.shader}.opacityB", lock=True)
        try:
            self.assertFalse(
                ShaderAttributeMap.connect_channel(
                    self.file_node, "opacity", self.shader
                )
            )
            for child in ("opacityR", "opacityG", "opacityB"):
                self.assertEqual(
                    self._sources(child), [], f"{child} left connected after rollback"
                )
        finally:
            cmds.setAttr(f"{self.shader}.opacityB", lock=False)


class TestOpenPBRLobes(MayaTkTestCase):
    """The lobe channels against a live openPBRSurface / standardSurface."""

    def _shader(self, node_type, name):
        try:
            return cmds.shadingNode(node_type, asShader=True, name=name)
        except RuntimeError as error:  # openPBR needs a recent 2025+
            self.skipTest(str(error))

    def test_every_declared_lobe_slot_exists_on_the_live_node(self):
        """The declarations are attribute names Maya really has (probed 2025.3)."""
        for node_type in ("openPBRSurface", "standardSurface"):
            shader = self._shader(node_type, f"lobe_{node_type}")
            for attr in ShaderAttributeMap.slot_attrs(node_type):
                if (
                    attr == "geometryNormal"
                ):  # the newer-Maya spelling; see SLOT_ALTERNATES
                    continue
                with self.subTest(node_type=node_type, attr=attr):
                    self.assertTrue(
                        cmds.attributeQuery(attr, node=shader, exists=True), attr
                    )

    def test_a_specular_level_texture_is_doubled_into_the_weight(self):
        """0.5 is the authored neutral, 1.0 the weight's: the file's own gain
        carries the factor, so the slot stays driven by the file directly."""
        for node_type in ("openPBRSurface", "standardSurface"):
            shader = self._shader(node_type, f"spec_{node_type}")
            file_node = self._file("/x/rock_Specular_Level.png")
            self.assertTrue(
                ShaderAttributeMap.connect_channel(file_node, "specularWeight", shader)
            )
            self.assertEqual(cmds.getAttr(f"{file_node}.colorGain")[0], (2.0, 2.0, 2.0))
            self.assertEqual(cmds.getAttr(f"{file_node}.alphaGain"), 2.0)
            # Re-wiring the same file must not compound the gain.
            ShaderAttributeMap.connect_channel(file_node, "specularWeight", shader)
            self.assertEqual(cmds.getAttr(f"{file_node}.alphaGain"), 2.0)

    def test_a_weight_unit_map_keeps_its_own_gain(self):
        """The gain belongs to the LEVEL map type, not the slot: a map already
        in weight units (hand-wired, or a legacy scene's) re-wired by a retype,
        a manifest replay or a transfer must not come back doubled."""
        shader = self._shader("openPBRSurface", "spec_weight_units")
        file_node = self._file("/x/rock_spec_weight.png")
        self.assertTrue(
            ShaderAttributeMap.connect_channel(file_node, "specularWeight", shader)
        )
        self.assertEqual(cmds.getAttr(f"{file_node}.colorGain")[0], (1.0, 1.0, 1.0))

    def test_texture_scale_converts_a_weight_unit_map_to_the_level(self):
        shader = self._shader("openPBRSurface", "scale_opbr")
        weight = self._file("/x/rock_spec_weight.png")
        ShaderAttributeMap.connect_channel(weight, "specularWeight", shader)
        self.assertEqual(
            ShaderAttributeMap.texture_scale(shader, "specularWeight"), 0.5
        )
        level = self._file("/x/rock_Specular_Level.png")
        ShaderAttributeMap.connect_channel(level, "specularWeight", shader)
        self.assertIsNone(ShaderAttributeMap.texture_scale(shader, "specularWeight"))
        self.assertIsNone(ShaderAttributeMap.texture_scale(shader, "roughness"))

    @staticmethod
    def _file(path):
        node = cmds.shadingNode("file", asTexture=True)
        cmds.setAttr(f"{node}.fileTextureName", path, type="string")
        return node

    def test_a_lobe_parameter_opens_its_weight_once(self):
        shader = self._shader("openPBRSurface", "gate_opbr")
        self.assertTrue(ShaderAttributeMap.open_lobe_gate(shader, "coatColor"))
        self.assertEqual(cmds.getAttr(f"{shader}.coatWeight"), 1.0)
        # An authored weight is the user's, not a default to overwrite.
        cmds.setAttr(f"{shader}.fuzzWeight", 0.3)
        self.assertFalse(ShaderAttributeMap.open_lobe_gate(shader, "sheenColor"))
        self.assertAlmostEqual(cmds.getAttr(f"{shader}.fuzzWeight"), 0.3, places=5)
        # A weight channel gates nothing itself.
        self.assertFalse(ShaderAttributeMap.open_lobe_gate(shader, "coat"))

    def test_a_driven_weight_is_left_to_its_map(self):
        shader = self._shader("openPBRSurface", "gate_driven")
        file_node = cmds.shadingNode("file", asTexture=True)
        cmds.connectAttr(f"{file_node}.outColorR", f"{shader}.coatWeight")
        self.assertFalse(ShaderAttributeMap.open_lobe_gate(shader, "coatRoughness"))

    def test_thin_walled_is_spelled_per_type(self):
        """OpenPBR's switch is ``geometryThinWalled``; probing ``thinWalled``
        alone left every retyped OpenPBR cutout solid."""
        for node_type, attr in (
            ("openPBRSurface", "geometryThinWalled"),
            ("standardSurface", "thinWalled"),
        ):
            shader = self._shader(node_type, f"thin_{node_type}")
            self.assertTrue(ShaderAttributeMap.set_thin_walled(shader))
            self.assertTrue(cmds.getAttr(f"{shader}.{attr}"), node_type)
        self.assertFalse(
            ShaderAttributeMap.set_thin_walled(
                cmds.shadingNode("lambert", asShader=True)
            )
        )

    def test_slot_attrs_include_the_alternates(self):
        attrs = ShaderAttributeMap.slot_attrs("openPBRSurface")
        self.assertIn("geometryNormal", attrs)
        self.assertIn("normalCamera", attrs)
        self.assertIn("geometryCoatNormal", attrs)
        self.assertEqual(len(attrs), len(set(attrs)))
        self.assertEqual(ShaderAttributeMap.slot_attrs("bogus"), ())


if __name__ == "__main__":
    unittest.main()
