# !/usr/bin/python
# coding=utf-8
"""Test Suite for mat_utils.shader_attribute_map.

The mapping itself is a pure-Python data module; ``connect_channel`` (and the
plug mechanics behind it) is the one Maya-touching part, covered by
:class:`TestConnectChannel` below.
"""

import unittest

import maya.cmds as cmds

from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap, ShaderAttrs

from base_test import MayaTkTestCase, QuickTestCase


class TestLogicalChannels(QuickTestCase):
    def test_returns_eight_known_channels(self):
        channels = ShaderAttributeMap.logical_channels()
        self.assertEqual(
            set(channels),
            {
                "baseColor",
                "emission",
                "specular",
                "roughness",
                "metallic",
                "opacity",
                "normal",
                "ambientOcclusion",
            },
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
            ["outAlpha"],
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


if __name__ == "__main__":
    unittest.main()
