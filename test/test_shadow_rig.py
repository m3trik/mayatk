# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.rig_utils.shadow_rig module

Tests for ShadowRig: rig build (source/contact/plane/material/texture +
keyable attrs), the expression against the shared projection model
(``pythontk.ShadowProjection`` — the MEL is a transcription of it, so every
placement is checked against the Python reference at several source
positions, a sun included), the physically projected silhouette (the plane
covers exactly the target's projected bounding box; an overhead source
draws the footprint), world-space light reads, the material wiring VP2
renders per pixel, the target/source stamps, re-attaching (for_node /
from_plane), the Utility operations (set_source, unbake, rebuild),
silhouette recalculation, bake-to-keyframes (with the fade, the rotation
unroll and the sparse visibility mirror), and the export metadata contract.

Reference geometry: a 2x2x2 cube at the origin -> contact (0, -1, 0),
objectHeight 2, footprintRadius = hypot(2, 2) / 2 = 1.4142, ground 0.
"""

import math
import os
import unittest

import maya.cmds as cmds
import numpy as np

try:
    from mayatk.rig_utils.shadow_rig import ShadowRig
except ImportError:
    import sys

    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from mayatk.rig_utils.shadow_rig import ShadowRig

from pythontk import ShadowAtlas, ShadowProjection
from base_test import MayaTkTestCase


class TestShadowRig(MayaTkTestCase):
    """Tests for the ShadowRig projected-shadow rig."""

    def setUp(self):
        super().setUp()
        # 2x2x2 cube centered at the origin (spans -1..1 on every axis).
        self.cube = cmds.polyCube(name="Box", width=2, height=2, depth=2)[0]
        self._textures = []

    def tearDown(self):
        for path in self._textures:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
        super().tearDown()

    def _make(self, **kwargs):
        kwargs.setdefault("light_pos", (5, 10, 5))
        kwargs.setdefault("texture_res", 64)
        rig = ShadowRig.create([self.cube], **kwargs)
        if rig.texture_path:
            self._textures.append(rig.texture_path)
        return rig

    def _sun(self, name="sunShape", rotate=(-45, 0, 0)):
        """A directional light (world -Z is its direction)."""
        shape = cmds.directionalLight(name=name)
        tf = cmds.listRelatives(shape, parent=True, fullPath=True)[0]
        cmds.setAttr(f"{tf}.rotate", *rotate, type="double3")
        return tf

    # ------------------------------------------------------ model reference
    @staticmethod
    def _plane_state(plane):
        return {
            ch: cmds.getAttr(f"{plane}.{ch}")
            for ch in (
                "translateX",
                "translateY",
                "translateZ",
                "rotateY",
                "scaleX",
                "scaleZ",
                "opacity",
            )
        }

    def assertPlaneMatchesModel(self, plane, places=3):
        """The expression's placement equals the pythontk model's for the
        rig re-attached from the plane (the MEL transcribes the Python)."""
        rig = ShadowRig.from_plane(plane)
        model = rig.current_model()
        (cx, cz), du, dw = model.placement(rig.canvas)
        state = self._plane_state(plane)
        self.assertAlmostEqual(state["translateX"], cx, places=places)
        self.assertAlmostEqual(state["translateZ"], cz, places=places)
        self.assertAlmostEqual(
            state["translateY"],
            rig.ground_height + ShadowRig.GROUND_OFFSET,
            places=places,
        )
        self.assertAlmostEqual(state["scaleZ"], du / rig.plane_size, places=places)
        self.assertAlmostEqual(state["scaleX"], dw / rig.plane_size, places=places)
        ux, uz = model.bearing
        self.assertAlmostEqual(
            state["rotateY"], math.degrees(math.atan2(ux, uz)), places=2
        )
        # The fade: elongation falloff x light-height fade x rise fade.
        contact = rig._contact_point()
        light_y = (
            cmds.xform(rig.light, q=True, ws=True, t=True)[1]
            if not ShadowRig.source_is_directional(rig.light)
            else contact[1] + 1.0  # a sun above the horizon: full
        )
        stretch = max(1.0, model.length / max(1e-4, 2.0 * rig.footprint_radius))
        intensity = cmds.getAttr(f"{plane}.shadowIntensity")
        power = cmds.getAttr(f"{plane}.falloffPower")
        fade_h = cmds.getAttr(f"{plane}.fadeHeight")
        expected = intensity / max(0.001, stretch**power)
        expected *= min(max(light_y - contact[1], 0.0), 1.0)
        expected *= min(
            max(
                1.0 - max(0.0, contact[1] - rig.ground_height) / max(0.001, fade_h), 0.0
            ),
            1.0,
        )
        self.assertAlmostEqual(
            state["opacity"], min(max(expected, 0.0), 1.0), places=places
        )
        return model

    # ------------------------------------------------------------------ build
    def test_build(self):
        """Rig build: nodes, keyable attrs, measured constants, fade channel,
        bearing + canvas stamps, texture."""
        rig = self._make()
        self.assertEqual(rig.mode, "orbit")
        self.assertNodeExists("shadow_source")
        self.assertNodeExists("Box_contact_loc")
        self.assertNodeExists("Box_shadow")
        self.assertNodeExists("Box_shadow_expr")
        self.assertNodeExists("Box_contact_dm")
        self.assertNodeExists("Box_light_dm")
        self.assertNodeExists("Box_shadow_grp")

        plane = rig.shadow_plane
        for attr, val in (
            ("shadowIntensity", 1.0),
            ("falloffPower", 1.2),
            ("maxStretch", ShadowProjection.DEFAULT_MAX_STRETCH),
            ("fadeHeight", 4.0),  # 2 x objectHeight
            ("groundHeight", 0.0),
            ("basePlaneSize", 1.0),
            ("objectHeight", 2.0),
            ("footprintRadius", math.hypot(2, 2) / 2),
        ):
            self.assertAlmostEqual(
                cmds.getAttr(f"{plane}.{attr}"), val, places=3, msg=attr
            )
        self.assertFalse(cmds.attributeQuery("scaleInfluence", node=plane, exists=True))
        # The fade channel is the RenderOpacity preset: keyable, 0..1.
        self.assertTrue(cmds.attributeQuery("opacity", node=plane, exists=True))
        self.assertTrue(cmds.getAttr(f"{plane}.opacity", keyable=True))
        self.assertEqual(
            cmds.attributeQuery("opacity", node=plane, maximum=True), [1.0]
        )
        # The raster bearing (source -> contact, unit 3D) is stamped.
        expected = np.array([0, -1, 0]) - np.array([5, 10, 5])
        expected = expected / np.linalg.norm(expected)
        for attr, val in zip(ShadowRig._BEARING_ATTRS, expected):
            self.assertAlmostEqual(cmds.getAttr(f"{plane}.{attr}"), val, places=3)
        # The canvas the PNG covers, as fractions of the model.
        u0, u1, w0, w1 = (cmds.getAttr(f"{plane}.{a}") for a in ShadowRig._CANVAS_ATTRS)
        self.assertLess(u0, u1)
        self.assertLess(w0, w1)
        self.assertEqual(rig.canvas, (u0, u1, w0, w1))
        self.assertTrue(rig.texture_path and os.path.exists(rig.texture_path))

    def test_material_wiring(self):
        """standardSurface with the silhouette in baseColor and
        ``file alpha x plane.opacity`` into opacity — the one wiring VP2
        renders per pixel (a Stingray transparent graph draws a solid
        square: measured 2026-09-02)."""
        rig = self._make()
        plane, shader, mult = rig.shadow_plane, rig.shader, rig.opacity_mult
        self.assertEqual(cmds.nodeType(shader), "standardSurface")
        file_node = ShadowRig._plane_texture_node(plane)
        self.assertEqual(cmds.nodeType(file_node), "file")
        self.assertEqual(
            cmds.listConnections(f"{shader}.baseColor", source=True, plugs=True),
            [f"{file_node}.outColor"],
        )
        self.assertEqual(
            cmds.listConnections(f"{mult}.input1X", source=True, plugs=True),
            [f"{file_node}.outAlpha"],
        )
        self.assertEqual(
            cmds.listConnections(f"{mult}.input2X", source=True, plugs=True),
            [f"{plane}.opacity"],
        )
        self.assertEqual(
            cmds.listConnections(f"{shader}.opacity", source=True, plugs=True),
            [f"{mult}.output"],
        )
        self.assertFalse(cmds.ls(type="StingrayPBS"))
        # A shadow reflects nothing.
        self.assertEqual(cmds.getAttr(f"{shader}.specular"), 0.0)

    def test_stretch_mode_is_an_orbit_alias(self):
        """The retired axis-aligned mode builds an orbit rig: the plane's
        local +Z points away from the light."""
        rig = self._make(mode="stretch")
        self.assertEqual(rig.mode, "orbit")
        self.assertAlmostEqual(
            cmds.getAttr(f"{rig.shadow_plane}.rotateY"), -135.0, places=1
        )

    def test_retired_stingray_shader_still_builds(self):
        """``shader_type='stingray'`` is kept for one release: it builds, with
        the silhouette bound as the colour map (the graph's only per-pixel
        alpha)."""
        rig = self._make(shader_type="stingray")
        self.assertEqual(cmds.nodeType(rig.shader), "StingrayPBS")
        file_node = ShadowRig._plane_texture_node(rig.shadow_plane)
        self.assertEqual(
            cmds.listConnections(
                f"{rig.shader}.TEX_color_map", source=True, plugs=True
            ),
            [f"{file_node}.outColor"],
        )
        self.assertEqual(cmds.getAttr(f"{rig.shader}.use_color_map"), 1.0)

    def test_explicit_axis_is_retired(self):
        """An explicit axis builds the same projected silhouette (warned, ignored)."""
        rig = self._make(axis="y")
        self.assertTrue(os.path.exists(rig.texture_path))
        self.assertPlaneMatchesModel(rig.shadow_plane)

    def test_namespaced_target(self):
        """A namespaced target builds with namespace-free rig node names and
        a legal texture filename (':' is invalid in Windows filenames)."""
        cmds.namespace(add="char")
        cube = cmds.polyCube(name="char:NsBox", width=2, height=2, depth=2)[0]
        rig = ShadowRig.create([cube], light_pos=(5, 10, 5), texture_res=64)
        if rig.texture_path:
            self._textures.append(rig.texture_path)
        self.assertNodeExists("NsBox_shadow")
        self.assertNodeExists("NsBox_shadow_grp")
        self.assertNotIn(":", os.path.basename(rig.texture_path))
        self.assertTrue(os.path.exists(rig.texture_path))

    def test_path_qualified_target(self):
        """Duplicate leaf names force a path-qualified target — '|' must not
        leak into rig node names (illegal in the name flag)."""
        grp = cmds.group(self.cube)
        cmds.polyCube(name="Box", width=2, height=2, depth=2)  # second root "Box"
        rig = ShadowRig.create([f"{grp}|Box"], light_pos=(5, 10, 5), texture_res=64)
        if rig.texture_path:
            self._textures.append(rig.texture_path)
        self.assertTrue(rig.shadow_plane and cmds.objExists(rig.shadow_plane))
        self.assertNotIn("|", os.path.basename(rig.texture_path))
        self.assertPlaneMatchesModel(rig.shadow_plane)

    def test_create_rollback_on_failure(self):
        """A failed create() (mesh-less target) rolls back every node it
        created — no orphan source/locator/plane left behind."""
        empty = cmds.spaceLocator(name="no_mesh_loc")[0]
        before = set(cmds.ls(long=True))
        with self.assertRaises(ValueError):
            ShadowRig.create([empty], texture_res=64)
        self.assertEqual(set(cmds.ls(long=True)), before)

    def test_source_name_conflict(self):
        """An existing non-transform node squatting the source name raises a
        clear ValueError (and rolls back) instead of failing mid-build."""
        cmds.createNode("multiplyDivide", name="taken_src")
        before = set(cmds.ls(long=True))
        with self.assertRaises(ValueError):
            ShadowRig.create([self.cube], source_name="taken_src", texture_res=64)
        self.assertEqual(set(cmds.ls(long=True)), before)

    def test_existing_transform_is_a_source(self):
        """Any transform is a valid source — a light included — and no
        locator is minted for it (the lights-from-geometry path). A light
        SHAPE's name resolves to its transform."""
        light = cmds.pointLight(name="keyLightShape")
        light_tf = cmds.listRelatives(light, parent=True)[0]
        rig = self._make(source_name=light)  # the shape's name
        self.assertEqual(rig.light, cmds.ls(light_tf, long=True)[0])
        self.assertFalse(cmds.ls("shadow_source"))
        # A non-default source joins the naming base: distinct nodes + PNG.
        self.assertNodeExists(f"Box_{light}_shadow")
        self.assertEqual(os.path.basename(rig.texture_path), f"Box_{light}_shadow.png")

    def test_create_for_sources(self):
        """N sources -> N planes on one target, each with its own PNG; the
        default source keeps the plain names."""
        rigs = ShadowRig.create_for_sources(
            [self.cube], ["shadow_source", "fillLight"], texture_res=64
        )
        for rig in rigs:
            self._textures.append(rig.texture_path)
        self.assertEqual(
            [rig.shadow_plane for rig in rigs], ["Box_shadow", "Box_fillLight_shadow"]
        )
        self.assertEqual(len(ShadowRig.find_shadow_planes()), 2)
        self.assertNotEqual(rigs[0].texture_path, rigs[1].texture_path)
        self.assertNodeExists("fillLight")

    # ------------------------------------------------------------- physics
    def test_expression_matches_the_projection_model(self):
        """The expression is the pythontk model: at every source position
        the plane's placement, scale, heading and fade equal the reference
        — including the lowering light growing the reach."""
        rig = self._make()
        plane = rig.shadow_plane
        reach_high = self.assertPlaneMatchesModel(plane).reach
        for pos in ((-4, 6, 2), (3, 3, -6), (0, 12, 0.5), (6, 2.5, 6)):
            cmds.setAttr("shadow_source.translate", *pos, type="double3")
            self.assertPlaneMatchesModel(plane)
        cmds.setAttr("shadow_source.translate", 5, 3, 5, type="double3")
        self.assertGreater(self.assertPlaneMatchesModel(plane).reach, reach_high)

    def test_plane_covers_the_projected_bounding_box(self):
        """The physics: the plane's world rectangle is the target's bounding
        box projected onto the ground through the source (plus the raster
        padding) — no wider, no shorter."""
        rig = self._make(light_pos=(-6, 5, 2))
        plane = rig.shadow_plane
        corners = np.array(
            [[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], dtype=float
        )
        ground, _ = ShadowProjection.project(corners, light=(-6, 5, 2), ground=0.0)
        model = ShadowRig.from_plane(plane).current_model()
        uw = ShadowProjection.to_frame(ground, model)
        # The plane's corners, world -> frame.
        m = np.array(cmds.xform(plane, q=True, ws=True, matrix=True)).reshape(4, 4)
        local = np.array([[x, 0, z, 1] for x in (-0.5, 0.5) for z in (-0.5, 0.5)])
        world = local @ m
        plane_uw = ShadowProjection.to_frame(world[:, [0, 2]], model)
        for axis in (0, 1):
            lo, hi = plane_uw[:, axis].min(), plane_uw[:, axis].max()
            self.assertLessEqual(lo, uw[:, axis].min() + 1e-3)
            self.assertGreaterEqual(hi, uw[:, axis].max() - 1e-3)
            extent = uw[:, axis].max() - uw[:, axis].min()
            self.assertLess((hi - lo) - extent, 0.12 * max(extent, 1.0) + 1e-3)

    def test_overhead_source_draws_the_footprint(self):
        """Straight above there is no bearing: the plane sits centred under
        the target, square, the size of the top face's projection — the
        footprint, not a side view — and the stamp points straight down."""
        rig = self._make(light_pos=(0, 10, 0))
        plane = rig.shadow_plane
        model = self.assertPlaneMatchesModel(plane)
        self.assertTrue(model.overhead)
        self.assertAlmostEqual(cmds.getAttr(f"{plane}.translateX"), 0.0, places=3)
        self.assertAlmostEqual(cmds.getAttr(f"{plane}.translateZ"), 0.0, places=3)
        self.assertAlmostEqual(cmds.getAttr(f"{plane}.rotateY"), 0.0, places=3)
        sx, sz = cmds.getAttr(f"{plane}.scaleX"), cmds.getAttr(f"{plane}.scaleZ")
        self.assertAlmostEqual(sx, sz, places=3)
        # The top face (y = 1) projects at k = 10/9 -> 2.22 wide, + padding.
        self.assertGreater(sx, 2.3)
        self.assertLess(sx, 2.6)
        for attr, val in zip(ShadowRig._BEARING_ATTRS, (0.0, -1.0, 0.0)):
            self.assertAlmostEqual(cmds.getAttr(f"{plane}.{attr}"), val, places=3)
        self.assertFalse(ShadowRig.silhouette_is_stale(plane))

    def test_sun_source_projects_along_its_direction(self):
        """A directional light is a sun: the rays are parallel to its world
        -Z, its position is irrelevant, and the reach is height x cot(elev)."""
        sun = self._sun(rotate=(-45, 0, 0))  # shines down and toward -Z
        rig = self._make(source_name=sun)
        plane = rig.shadow_plane
        self.assertTrue(ShadowRig.source_is_directional(sun))
        self.assertNodeExists(f"Box_{cmds.ls(sun)[0].split('|')[-1]}_light_vp")
        self.assertFalse(cmds.objExists("Box_light_dm"))
        model = self.assertPlaneMatchesModel(plane)
        self.assertAlmostEqual(model.reach, 2.0, places=2)  # 2 x cot 45
        self.assertAlmostEqual(model.bearing[1], -1.0, places=3)
        self.assertAlmostEqual(cmds.getAttr(f"{plane}.rotateY"), 180.0, places=1)
        # Moving the sun changes nothing; rotating it does.
        before = self._plane_state(plane)
        cmds.setAttr(f"{sun}.translate", 8, 3, -2, type="double3")
        after = self._plane_state(plane)
        for ch in ("translateX", "translateZ", "scaleZ", "rotateY"):
            self.assertAlmostEqual(after[ch], before[ch], places=4, msg=ch)
        cmds.setAttr(f"{sun}.rotate", -30, 0, 0, type="double3")
        self.assertGreater(self.assertPlaneMatchesModel(plane).reach, 2.0)

    def test_projected_anchor_and_rise_fade(self):
        """Rising target: the shadow slides away from the light (the anchor
        follows the ray through the contact) and fades with the rise."""
        rig = self._make()
        plane = rig.shadow_plane
        opacity_grounded = cmds.getAttr(f"{plane}.opacity")
        self.assertGreater(opacity_grounded, 0.0)

        cmds.setAttr(f"{self.cube}.translateY", 3)  # contact y = 2
        model = self.assertPlaneMatchesModel(plane)
        self.assertAlmostEqual(model.k_base, 10.0 / 8.0, places=4)
        self.assertAlmostEqual(model.anchor[0], 5 - 5 * 1.25, places=4)
        self.assertLess(cmds.getAttr(f"{plane}.opacity"), opacity_grounded * 0.55)

    def _edge_x(self, plane, local_z):
        """World X of the plane's near (``local_z = -0.5``) or far (``+0.5``)
        edge centre — local -Z is the light-side edge (see the file node's V)."""
        m = np.array(cmds.xform(plane, q=True, ws=True, matrix=True)).reshape(4, 4)
        return float((np.array([0.0, 0.0, local_z, 1.0]) @ m)[0])

    def test_shadow_stays_attached_to_the_feet_as_the_source_lowers(self):
        """The reported gap (screenshots): rasterized under a high source the
        plane's near edge sat at the box's near face, then slid away from the
        feet as the source lowered — the canvas was stamped as fractions of the
        model's LENGTH. The near edge is pinned to the footprint at every
        source height, and the far edge lands where the top's far corner
        projects: at (6, 4, 0) the top (y = 2) projects at t = 2, so the far
        corner x = -1 lands at -1 + (-1 - 6) x 1 = -8."""
        cmds.setAttr(f"{self.cube}.translateY", 1)  # resting on the ground
        rig = self._make(light_pos=(6, 20, 0))
        plane = rig.shadow_plane
        near_high = self._edge_x(plane, -0.5)
        self.assertLess(abs(near_high - 1.0), 0.2)  # the near face (+X), plus padding
        cmds.setAttr(f"{rig.light}.translate", 6, 4, 0, type="double3")
        self.assertAlmostEqual(self._edge_x(plane, -0.5), near_high, places=3)
        self.assertLess(abs(self._edge_x(plane, 0.5) + 8.0), 0.3)
        self.assertPlaneMatchesModel(plane)

    def test_light_world_space(self):
        """The expression reads the light's WORLD position — moving a parent
        group of the light must warp the shadow (raw .translate is local)."""
        rig = self._make()
        plane = rig.shadow_plane
        before = cmds.getAttr(f"{plane}.rotateY")
        grp = cmds.group("shadow_source", name="light_grp")
        cmds.setAttr(f"{grp}.translateX", 5)  # light world x: 5 -> 10
        self.assertPlaneMatchesModel(plane)
        self.assertNotAlmostEqual(cmds.getAttr(f"{plane}.rotateY"), before, places=1)

    def test_ground_height_attr_drives_the_plane(self):
        """groundHeight is an attribute the expression reads, so a raised
        floor is a post-create edit: the plane sits on it and the shadow
        projects onto it."""
        rig = self._make()
        plane = rig.shadow_plane
        cmds.setAttr(f"{plane}.groundHeight", 2.0)
        self.assertAlmostEqual(
            cmds.getAttr(f"{plane}.translateY"), 2.0 + ShadowRig.GROUND_OFFSET, places=3
        )
        model = self.assertPlaneMatchesModel(plane)
        self.assertAlmostEqual(model.k_base, 8.0 / 11.0, places=4)

    def test_area_light_size_draws_a_penumbra(self):
        """A sized source (an area light, 2x2 local x its scale) is stamped
        and softens the silhouette away from the contact: more partial
        alpha than a point source gives."""
        from PIL import Image

        sharp = self._make()
        sharp_alpha = np.asarray(Image.open(sharp.texture_path))[:, :, 3]
        area = cmds.shadingNode("areaLight", asLight=True)
        cmds.setAttr(f"{area}.translate", 5, 10, 5, type="double3")
        cmds.setAttr(f"{area}.scale", 2, 2, 2, type="double3")
        soft = self._make(source_name=area)
        self.assertAlmostEqual(
            cmds.getAttr(f"{soft.shadow_plane}.sourceSize"), 4.0, places=3
        )
        soft_alpha = np.asarray(Image.open(soft.texture_path))[:, :, 3]

        def partial(a):
            return ((a > 12) & (a < 243)).sum()

        self.assertGreater(partial(soft_alpha), partial(sharp_alpha) * 1.3)

    # ------------------------------------------------------------------ stamps / re-attach
    def test_rig_links_stamped(self):
        """The plane links its targets and source by message — the handles
        Recalculate needs once the Python instance is gone — and a rig
        re-attached from the plane resolves every node it needs."""
        rig = self._make()
        targets, source = ShadowRig._rig_links(rig.shadow_plane)
        self.assertEqual(targets, cmds.ls(self.cube, long=True))
        self.assertEqual(source, cmds.ls("shadow_source", long=True)[0])
        again = ShadowRig.from_plane(rig.shadow_plane)
        self.assertEqual(again.targets, targets)
        self.assertEqual(again.light, source)
        self.assertEqual(again._name_base, "Box")
        self.assertEqual(
            again.contact_locator, cmds.ls("Box_contact_loc", long=True)[0]
        )
        self.assertEqual(again.group, cmds.ls("Box_shadow_grp", long=True)[0])
        self.assertEqual(again.shader, rig.shader)
        self.assertEqual(again.opacity_mult, rig.opacity_mult)
        self.assertEqual(again.texture_path, rig.texture_path)
        self.assertEqual(again.canvas, rig.canvas)
        self.assertAlmostEqual(again.footprint_radius, rig.footprint_radius, places=5)
        self.assertIs(ShadowRig._from_plane.__func__, ShadowRig.from_plane.__func__)

    def test_for_node_resolves_from_any_rig_node(self):
        """for_node re-attaches the rig from the plane, its group, a target,
        the source (or its shape), the contact locator or a shading node;
        nothing for an unrelated node."""
        rig = self._make()
        plane = cmds.ls(rig.shadow_plane, long=True)[0]
        for node in (
            rig.shadow_plane,
            "Box_shadow_grp",
            self.cube,
            "shadow_source",
            cmds.listRelatives("shadow_source", shapes=True)[0],
            "Box_contact_loc",
            rig.shader,
        ):
            found = ShadowRig.for_node(node)
            self.assertIsNotNone(found, node)
            self.assertEqual(cmds.ls(found.shadow_plane, long=True)[0], plane, node)
        other = cmds.polyCube(name="Other")[0]
        self.assertIsNone(ShadowRig.for_node(other))
        self.assertEqual(
            len(ShadowRig.planes_for_nodes([self.cube, "shadow_source"])), 1
        )
        self.assertEqual(len(ShadowRig.for_nodes([self.cube, other])), 1)

    def test_refresh_silhouette_follows_the_moved_source(self):
        """Moving the source past the bearing tolerance marks the silhouette
        stale; Recalculate re-rasterizes the same PNG in place and restamps
        the bearing — refitting a live rig's canvas, and drawing into a
        BAKED rig's existing canvas (its keys already place the plane)."""
        # A cube reads the same from every bearing; a post off to one side
        # makes the opposite bearing a different silhouette.
        post = cmds.polyCube(name="Post", width=0.6, height=4, depth=0.6)[0]
        cmds.setAttr(f"{post}.translate", 0.7, 1.0, -0.7, type="double3")
        cmds.parent(post, self.cube)
        rig = self._make()
        plane = rig.shadow_plane
        path = rig.texture_path
        canvas = rig.canvas
        before = open(path, "rb").read()
        self.assertFalse(ShadowRig.silhouette_is_stale(plane))

        cmds.setAttr("shadow_source.translate", -5, 10, -5, type="double3")
        self.assertTrue(ShadowRig.silhouette_is_stale(plane))

        # Live: the canvas is refitted and the plane follows.
        self.assertEqual(ShadowRig.refresh_silhouette([plane]), [plane])
        self.assertFalse(ShadowRig.silhouette_is_stale(plane))
        refit = tuple(cmds.getAttr(f"{plane}.{a}") for a in ShadowRig._CANVAS_ATTRS)
        self.assertNotEqual(refit, canvas)
        self.assertPlaneMatchesModel(plane)
        self.assertNotEqual(open(path, "rb").read(), before)

        # Baked: the PNG is redrawn, the canvas stamps stay (the keys own the placement).
        cmds.setAttr("shadow_source.translate", 5, 10, 5, type="double3")
        cmds.playbackOptions(min=1, max=2)
        rig.bake(1, 2)  # no expression, no driver nodes any more
        self.assertTrue(ShadowRig.silhouette_is_stale(plane))
        self.assertEqual(ShadowRig.refresh_silhouette([plane]), [plane])
        self.assertEqual(ShadowRig._plane_texture_path(plane), path)
        self.assertEqual(
            tuple(cmds.getAttr(f"{plane}.{a}") for a in ShadowRig._CANVAS_ATTRS), refit
        )
        self.assertFalse(ShadowRig.silhouette_is_stale(plane))
        bx = cmds.getAttr(f"{plane}.silhouetteBearingX")
        self.assertAlmostEqual(bx, -5 / math.sqrt(25 + 121 + 25), places=3)

    def test_refresh_silhouette_skips_unstamped_rigs(self):
        """A plane built before the stamps has nothing to recompute from."""
        rig = self._make()
        plane = rig.shadow_plane
        cmds.deleteAttr(f"{plane}.{ShadowRig._TARGETS_ATTR}")
        self.assertEqual(ShadowRig.refresh_silhouette([plane]), [])
        self.assertFalse(ShadowRig.silhouette_is_stale(plane))
        self.assertIsNone(ShadowRig.for_node(plane))

    # ------------------------------------------------------------------ utility
    def test_set_source_relinks_and_reprojects(self):
        """set_source re-points a rig at another source — a sun here — and
        rebuilds the expression and silhouette for it; the new driver node
        joins the teardown manifest."""
        rig = self._make()
        plane = rig.shadow_plane
        sun = self._sun(rotate=(-60, 20, 0))
        rig.set_source(sun, size=64)
        _, source = ShadowRig._rig_links(plane)
        self.assertEqual(source, sun)
        self.assertNodeExists("Box_light_vp")
        self.assertFalse(cmds.objExists("Box_light_dm"))
        self.assertPlaneMatchesModel(plane)
        ShadowRig.delete_rigs([plane])
        self.assertFalse(cmds.objExists("Box_light_vp"))
        self.assertTrue(cmds.objExists(sun))

    def test_unbake_restores_the_expression(self):
        """unbake_planes reverses a bake: keys gone, expression and driver
        nodes back, the plane following the source again."""
        rig = self._make()
        plane = rig.shadow_plane
        cmds.playbackOptions(min=1, max=3)
        rig.bake(1, 3)
        self.assertTrue(ShadowRig.plane_is_baked(plane))
        self.assertEqual(ShadowRig.unbake_planes([plane]), [plane])
        self.assertFalse(ShadowRig.plane_is_baked(plane))
        self.assertTrue(ShadowRig.plane_is_live(plane))
        self.assertNodeExists("Box_shadow_expr")
        self.assertNodeExists("Box_light_dm")
        before = cmds.getAttr(f"{plane}.rotateY")
        cmds.setAttr("shadow_source.translate", -5, 10, 5, type="double3")
        self.assertNotAlmostEqual(cmds.getAttr(f"{plane}.rotateY"), before, places=1)
        self.assertPlaneMatchesModel(plane)
        # A second call finds nothing baked.
        self.assertEqual(ShadowRig.unbake_planes([plane]), [])
        # set_source on a BAKED plane restores the expression itself.
        rig.bake(1, 3)
        rig.set_source("shadow_source", size=64)
        self.assertTrue(ShadowRig.plane_is_live(plane))

    def test_rebuild_keeps_the_name_and_reads_the_new_geometry(self):
        """rebuild tears the rig down and builds it again from its stamps
        against the target's CURRENT geometry, keeping the plane's name."""
        rig = self._make()
        self._textures.append(rig.texture_path)
        cmds.setAttr(f"{self.cube}.scaleY", 2)  # now 4 tall
        new = ShadowRig.rebuild(rig.shadow_plane, texture_res=32)
        self.assertIsNotNone(new)
        self.assertEqual(new.shadow_plane, "Box_shadow")
        self.assertFalse(cmds.objExists("Box_shadow1"))
        self.assertAlmostEqual(cmds.getAttr("Box_shadow.objectHeight"), 4.0, places=3)
        self.assertEqual(
            ShadowRig._rig_links("Box_shadow")[1],
            cmds.ls("shadow_source", long=True)[0],
        )
        self.assertEqual(len(ShadowRig.find_shadow_planes()), 1)
        self.assertEqual(ShadowRig._texture_size(new.texture_path), 32)

    # ------------------------------------------------------------------ bake
    def test_bake(self):
        """bake() keys the driven channels AND the fade over the range and
        removes the expression + driver nodes; values survive the bake."""
        rig = self._make()
        plane = rig.shadow_plane
        driven_tx = cmds.getAttr(f"{plane}.translateX")
        driven_opacity = cmds.getAttr(f"{plane}.opacity")

        cmds.playbackOptions(min=1, max=3)
        baked = rig.bake(1, 3)
        self.assertEqual(baked, [plane])
        self.assertFalse(cmds.objExists("Box_shadow_expr"))
        self.assertFalse(cmds.objExists("Box_contact_dm"))
        self.assertFalse(cmds.objExists("Box_light_dm"))
        self.assertEqual(
            cmds.keyframe(f"{plane}.translateX", q=True, keyframeCount=True), 3
        )
        self.assertEqual(
            cmds.keyframe(f"{plane}.opacity", q=True, keyframeCount=True), 3
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{plane}.translateX", time=2), driven_tx, places=3
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{plane}.opacity", time=2), driven_opacity, places=3
        )
        # A second bake finds no live expression -> no-op.
        self.assertEqual(ShadowRig.bake_planes([plane]), [])

    def test_bake_unrolls_rotation(self):
        """A light crossing behind the target wraps atan2 at +-180: the baked
        rotateY is euler-unrolled into a continuous curve."""
        rig = self._make(light_pos=(2, 10, 5))
        plane = rig.shadow_plane
        cmds.setKeyframe("shadow_source", attribute="translateX", t=1, v=2)
        cmds.setKeyframe("shadow_source", attribute="translateX", t=3, v=-2)
        cmds.playbackOptions(min=1, max=3)
        rig.bake(1, 3)
        values = cmds.keyframe(f"{plane}.rotateY", q=True, valueChange=True)
        self.assertEqual(len(values), 3)
        jumps = [abs(b - a) for a, b in zip(values, values[1:])]
        self.assertLess(max(jumps), 90.0, values)

    def test_bake_mirrors_fade_to_sparse_visibility(self):
        """A baked fade that reaches zero (the target rising past fadeHeight)
        leaves SPARSE visibility keys bounding the fade — the pairs Unity's
        opacity importer rebuilds a fade from — not one key per frame."""
        rig = self._make()
        plane = rig.shadow_plane
        cmds.setKeyframe(self.cube, attribute="translateY", t=1, v=0)
        cmds.setKeyframe(self.cube, attribute="translateY", t=12, v=10)
        cmds.setKeyframe(self.cube, attribute="translateY", t=24, v=0)
        cmds.playbackOptions(min=1, max=24)
        rig.bake(1, 24)
        self.assertEqual(cmds.getAttr(f"{plane}.opacity", time=12), 0.0)
        vis_values = cmds.keyframe(f"{plane}.visibility", q=True, valueChange=True)
        self.assertTrue(vis_values)
        self.assertLessEqual(len(vis_values), 6, vis_values)
        self.assertIn(0.0, vis_values)
        self.assertIn(1.0, vis_values)

    def test_bake_planes_batch(self):
        """bake_planes(None) bakes every live rig in a single pass."""
        rig1 = self._make()
        cube2 = cmds.polyCube(name="Box2", width=2, height=2, depth=2)[0]
        rig2 = ShadowRig.create([cube2], light_pos=(5, 10, 5), texture_res=64)
        if rig2.texture_path:
            self._textures.append(rig2.texture_path)
        cmds.playbackOptions(min=1, max=3)
        baked = ShadowRig.bake_planes()
        self.assertEqual(sorted(baked), sorted([rig1.shadow_plane, rig2.shadow_plane]))
        for plane in (rig1.shadow_plane, rig2.shadow_plane):
            self.assertEqual(
                cmds.keyframe(f"{plane}.translateX", q=True, keyframeCount=True), 3
            )
            self.assertFalse(
                cmds.listConnections(
                    plane, source=True, destination=False, type="expression"
                )
            )

    # ------------------------------------------------------------------ delete
    def test_delete_rigs(self):
        """delete_rigs tears down the WHOLE rig — plane, group, expression,
        driver nodes, shading network, contact locator — clears the
        metadata channel, and leaves the target + shared source untouched."""
        from mayatk.node_utils.data_nodes import DataNodes

        rig = self._make()
        doomed = (
            "Box_shadow",
            "Box_shadow_grp",
            "Box_shadow_expr",
            "Box_contact_dm",
            "Box_light_dm",
            "Box_contact_loc",
            "Box_shadow_mat",
            "Box_shadow_mat_SG",
            "Box_shadow_tex",
            "Box_shadow_place2d",
            "Box_shadow_opacity_mult",
        )
        deleted = ShadowRig.delete_rigs([rig.shadow_plane])
        self.assertEqual(deleted, [rig.shadow_plane])
        for node in doomed:
            self.assertFalse(cmds.objExists(node), node)
        self.assertTrue(cmds.objExists(self.cube))
        self.assertTrue(cmds.objExists("shadow_source"))
        self.assertIsNone(DataNodes.get_export_string(ShadowRig.SHADOW_METADATA))

        # A BAKED rig (expression already gone) still tears down fully, and
        # delete_textures removes the silhouette PNG from disk.
        rig2 = self._make()
        cmds.playbackOptions(min=1, max=2)
        rig2.bake(1, 2)
        tex = rig2.texture_path
        self.assertTrue(os.path.exists(tex))
        rig2.delete(delete_textures=True)
        self.assertFalse(cmds.objExists("Box_shadow"))
        self.assertFalse(cmds.objExists("Box_contact_loc"))
        self.assertFalse(os.path.exists(tex))

    def test_referenced_rig_skipped(self):
        """A REFERENCED shadow plane can't have its rig nodes deleted from
        the referencing scene — bake_planes and delete_rigs must skip it
        (with a warning) instead of erroring mid-batch."""
        self._make()
        temp_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "temp_tests"
        )
        os.makedirs(temp_dir, exist_ok=True)
        scene = os.path.join(temp_dir, "shadow_ref_src.ma")
        cmds.file(rename=scene)
        cmds.file(save=True, type="mayaAscii", force=True)
        self.addCleanup(lambda: os.remove(scene) if os.path.exists(scene) else None)
        cmds.file(new=True, force=True)
        cmds.file(scene, reference=True, namespace="ref")

        planes = ShadowRig.find_shadow_planes()
        self.assertEqual(len(planes), 1)
        self.assertEqual(ShadowRig.bake_planes(), [])
        self.assertEqual(ShadowRig.delete_rigs(), [])
        self.assertTrue(cmds.objExists(planes[0]))

    def test_find_shadow_planes(self):
        """Planes are found by the stamped basePlaneSize attr, including via
        a selected ancestor (the *_shadow_grp); a target alone is not a
        plane (planes_for_nodes follows the links instead)."""
        rig = self._make()
        self.assertIn(rig.shadow_plane, ShadowRig.find_shadow_planes())
        self.assertIn(
            rig.shadow_plane, ShadowRig.find_shadow_planes(["Box_shadow_grp"])
        )
        self.assertEqual(ShadowRig.find_shadow_planes([self.cube]), [])
        self.assertEqual(ShadowRig.planes_for_nodes([self.cube]), [rig.shadow_plane])

    # ------------------------------------------------------------------ export metadata
    def test_export_metadata(self):
        """create()/bake() publish the shadow_metadata channel on the
        data_export carrier (the Scene Exporter hand-off contract); a
        plane-less refresh clears it."""
        import json

        from mayatk.node_utils.data_nodes import DataNodes

        rig = self._make()
        payload = json.loads(DataNodes.get_export_string(ShadowRig.SHADOW_METADATA))
        self.assertEqual(payload["version"], ShadowRig.METADATA_VERSION)
        self.assertAlmostEqual(payload["unit_scale"], 0.01, places=6)  # cm scene
        recs = {r["name"]: r for r in payload["planes"]}
        self.assertIn("Box_shadow", recs)
        rec = recs["Box_shadow"]
        self.assertEqual(rec["texture"], "Box_shadow.png")
        self.assertAlmostEqual(rec["intensity"], 1.0, places=3)
        # v2: the engine's runtime inputs ride the record (the contract in
        # mayatk/docs/shadow_rig_morphing.md).
        self.assertEqual(rec["type"], "projected")
        self.assertEqual(rec["source"], ShadowRig.DEFAULT_SOURCE_NAME)
        self.assertEqual(rec["source_type"], "point")
        self.assertTrue(rec["follow_source"])
        self.assertEqual(rec["contact"], "Box_contact_loc")
        self.assertAlmostEqual(rec["radius"], math.hypot(2, 2) / 2, places=3)
        self.assertAlmostEqual(rec["height"], 2.0, places=3)
        self.assertAlmostEqual(rec["max_stretch"], 6.0, places=3)
        self.assertEqual(len(rec["canvas"]), 4)
        self.assertNotIn("atlas", rec)
        self.assertNotIn("horizon", rec)
        # Classmethod texture resolution (feeds the record) works off the plane.
        tex = ShadowRig._plane_texture_path(rig.shadow_plane)
        self.assertTrue(tex and os.path.basename(tex) == "Box_shadow.png")

        # Bake re-refreshes the channel (still one record, expression gone).
        cmds.playbackOptions(min=1, max=3)
        rig.bake(1, 3)
        payload = json.loads(DataNodes.get_export_string(ShadowRig.SHADOW_METADATA))
        self.assertEqual(len(payload["planes"]), 1)

        # Removing the rig clears the channel on the next refresh
        # (run_export_preparers does this via the known-producer registry).
        cmds.delete("Box_shadow_grp")
        ShadowRig.refresh_export_metadata()
        self.assertIsNone(DataNodes.get_export_string(ShadowRig.SHADOW_METADATA))

    # ------------------------------------------------------------------ horizon rig
    def _record(self, plane):
        import json

        from mayatk.node_utils.data_nodes import DataNodes

        payload = json.loads(DataNodes.get_export_string(ShadowRig.SHADOW_METADATA))
        return {r["name"]: r for r in payload["planes"]}[plane]

    def test_horizon_rig_bakes_a_map_and_records_it(self):
        """rig_type="horizon" adds the horizon PNG beside the silhouette, the
        type stamp and the record's horizon block (bins, tile, layout, the
        log-polar range, the frame, an identity rect); the plane, expression
        and silhouette are the projected rig's."""
        rig = self._make(rig_type="horizon", horizon_bins=8, horizon_size=(32, 16))
        self._textures.append(rig.horizon_path)
        self.assertEqual(rig.rig_type, "horizon")
        self.assertTrue(rig.horizon_path.endswith("Box_horizon.png"))
        self.assertTrue(os.path.exists(rig.horizon_path))
        self.assertEqual(ShadowRig.plane_type(rig.shadow_plane), "horizon")
        self.assertPlaneMatchesModel(rig.shadow_plane)
        rec = self._record("Box_shadow")
        self.assertEqual(rec["type"], "horizon")
        self.assertEqual(rec["texture"], "Box_shadow.png")
        hz = rec["horizon"]
        self.assertEqual(hz["texture"], "Box_horizon.png")
        self.assertEqual((hz["bins"], hz["layers"], hz["tile"]), (8, 2, [32, 16]))
        self.assertEqual(hz["layout"], [4, 4])
        self.assertEqual(hz["mapping"], "logpolar")
        self.assertGreater(hz["r_max"], hz["r_min"] > 0)
        # The map's own encode scale, apart from the plane's live maxStretch.
        self.assertAlmostEqual(hz["max_stretch"], 6.0, places=6)
        self.assertEqual((hz["frame_a"], hz["frame_b"]), ([1, 0, 0], [0, 0, 1]))
        self.assertEqual(hz["rect"], [1.0, 1.0, 0.0, 0.0])
        # The PNG holds the 2 x bins tiles the map's layout says.
        from PIL import Image

        with Image.open(rig.horizon_path) as im:
            self.assertEqual(im.size, (4 * 32, 4 * 16))
        # from_plane restores the type and the map path.
        again = ShadowRig.from_plane(rig.shadow_plane)
        self.assertEqual(
            (again.rig_type, again.horizon_path), ("horizon", rig.horizon_path)
        )

    def test_recalculate_rebakes_the_map_only_when_the_geometry_changed(self):
        rig = self._make(rig_type="horizon", horizon_bins=8, horizon_size=(32, 16))
        self._textures.append(rig.horizon_path)
        before = os.path.getmtime(rig.horizon_path)
        ShadowRig.refresh_silhouette([rig.shadow_plane])
        self.assertEqual(os.path.getmtime(rig.horizon_path), before)
        cmds.setAttr(f"{self.cube}.scaleY", 2.0)
        ShadowRig.refresh_silhouette([rig.shadow_plane])
        self.assertGreater(os.path.getmtime(rig.horizon_path), before)
        # Retuning maxStretch changes what the map's cotangents mean, so it
        # re-bakes too — and the record carries the new encode scale.
        after = os.path.getmtime(rig.horizon_path)
        cmds.setAttr(f"{rig.shadow_plane}.maxStretch", 3.0)
        ShadowRig.refresh_silhouette([rig.shadow_plane])
        self.assertGreater(os.path.getmtime(rig.horizon_path), after)
        rec = self._record("Box_shadow")
        self.assertAlmostEqual(rec["horizon"]["max_stretch"], 3.0, places=6)
        self.assertAlmostEqual(rec["max_stretch"], 3.0, places=6)

    def test_rebuild_keeps_the_horizon_type(self):
        rig = self._make(rig_type="horizon", horizon_bins=8, horizon_size=(32, 16))
        self._textures.append(rig.horizon_path)
        rebuilt = ShadowRig.rebuild(rig.shadow_plane)
        self.assertEqual(rebuilt.rig_type, "horizon")
        self.assertEqual(self._record("Box_shadow")["horizon"]["bins"], 8)

    # ------------------------------------------------------------------ per object + atlas
    def test_per_object_builds_one_rig_per_target(self):
        other = cmds.polyCube(name="Crate", width=1, height=1, depth=1)[0]
        cmds.setAttr(f"{other}.translateX", 4.0)
        rigs = ShadowRig.create_per_object(
            [self.cube, other], [ShadowRig.DEFAULT_SOURCE_NAME], texture_res=32
        )
        self._textures.extend(r.texture_path for r in rigs)
        self.assertEqual(
            [r.shadow_plane.split("|")[-1] for r in rigs],
            ["Box_shadow", "Crate_shadow"],
        )
        self.assertEqual(
            sorted(self._record(n)["contact"] for n in ("Box_shadow", "Crate_shadow")),
            ["Box_contact_loc", "Crate_contact_loc"],
        )

    def test_pack_atlas_remaps_uvs_and_rewrites_tiles_in_place(self):
        """Two rigs pack into one silhouette atlas: each plane's UVs land in
        its inset rect, its file node names the atlas, its record carries
        the rect, and Recalculate rewrites its tile without moving the
        other's."""
        from PIL import Image

        other = cmds.polyCube(name="Crate", width=1, height=1, depth=1)[0]
        cmds.setAttr(f"{other}.translateX", 4.0)
        rigs = ShadowRig.create_per_object(
            [self.cube, other], ["shadow_source"], texture_res=32
        )
        self._textures.extend(r.texture_path for r in rigs)
        packed = ShadowRig.pack_atlas([r.shadow_plane for r in rigs])
        atlas = packed["projected"]
        self._textures.append(atlas)
        self.assertTrue(atlas.endswith("shadow_atlas_projected.png"))
        with Image.open(atlas) as im:
            self.assertEqual(im.size, (64, 32))  # two 32 px cells, side by side
        box, crate = rigs[0].shadow_plane, rigs[1].shadow_plane
        self.assertTrue(ShadowRig.plane_is_atlased(box))
        node = ShadowRig._plane_texture_node(box)
        self.assertEqual(
            os.path.basename(cmds.getAttr(f"{node}.fileTextureName")),
            "shadow_atlas_projected.png",
        )
        # The plane's own PNG is still what the record and Recalculate use.
        self.assertEqual(
            os.path.basename(ShadowRig._plane_texture_path(box)), "Box_shadow.png"
        )
        rec = self._record("Box_shadow")
        self.assertEqual(rec["texture"], "Box_shadow.png")
        self.assertEqual(rec["atlas"]["texture"], "shadow_atlas_projected.png")
        sx, sy, ox, oy = rec["atlas"]["rect"]
        self.assertLess(sx, 0.5)  # a gutter-inset half
        us = [cmds.polyEditUV(f"{box}.map[{i}]", query=True) for i in range(4)]
        for u, v in us:
            self.assertGreaterEqual(u, ox - 1e-6)
            self.assertLessEqual(u, ox + sx + 1e-6)
            self.assertGreaterEqual(v, oy - 1e-6)
            self.assertLessEqual(v, oy + sy + 1e-6)

        # Each cell holds its plane's own tile, and Recalculate rewrites only
        # that plane's cell — the tile is re-rastered into its own PNG and
        # copied into the atlas in place (never over the atlas itself, which
        # is what the plane's file node now names).
        def cells():
            with Image.open(atlas) as im:
                data = np.asarray(im.convert("RGBA")).copy()
            return data[0:32, 0:32], data[0:32, 32:64]

        def tile(plane):
            with Image.open(ShadowRig._plane_texture_path(plane)) as im:
                return np.asarray(im.convert("RGBA")).copy()

        box_cell, crate_cell = cells()
        self.assertTrue((box_cell == tile(box)).all())
        self.assertTrue((crate_cell == tile(crate)).all())
        crate_before = crate_cell
        cmds.setAttr("shadow_source.translateX", -6.0)
        ShadowRig.refresh_silhouette([box])
        box_cell, crate_cell = cells()
        with Image.open(atlas) as im:
            self.assertEqual(im.size, (64, 32))  # the atlas was not overwritten
        self.assertTrue((box_cell == tile(box)).all())
        self.assertTrue((crate_cell == crate_before).all())
        self.assertEqual(
            os.path.basename(cmds.getAttr(f"{node}.fileTextureName")),
            "shadow_atlas_projected.png",
        )
        # Unpack restores unit UVs and the plane's own PNG.
        ShadowRig.unpack_atlas([box, crate])
        self.assertFalse(ShadowRig.plane_is_atlased(box))
        self.assertEqual(
            os.path.basename(cmds.getAttr(f"{node}.fileTextureName")), "Box_shadow.png"
        )
        self.assertEqual(
            sorted(
                tuple(cmds.polyEditUV(f"{box}.map[{i}]", query=True)) for i in range(4)
            ),
            [(0.0, 0.0), (0.0, 1.0), (1.0, 0.0), (1.0, 1.0)],
        )
        self.assertNotIn("atlas", self._record("Box_shadow"))

    def test_a_packed_plane_whose_tile_vanished_leaves_the_atlas(self):
        """Its rect would otherwise be handed to another plane by the next
        repack, and it would wear that plane's shadow."""
        other = cmds.polyCube(name="Crate", width=1, height=1, depth=1)[0]
        cmds.setAttr(f"{other}.translateX", 4.0)
        rigs = ShadowRig.create_per_object(
            [self.cube, other], ["shadow_source"], texture_res=32
        )
        self._textures.extend(r.texture_path for r in rigs)
        box, crate = rigs[0].shadow_plane, rigs[1].shadow_plane
        self._textures.append(ShadowRig.pack_atlas([box, crate])["projected"])
        os.remove(ShadowRig._plane_texture_path(box))
        ShadowRig.pack_atlas([crate])
        self.assertFalse(ShadowRig.plane_is_atlased(box))
        self.assertNotIn("atlas", self._record("Box_shadow"))
        self.assertEqual(
            sorted(
                tuple(cmds.polyEditUV(f"{box}.map[{i}]", query=True)) for i in range(4)
            ),
            [(0.0, 0.0), (0.0, 1.0), (1.0, 0.0), (1.0, 1.0)],
        )
        # The survivor keeps its own tile, now the only one in the atlas: a
        # single 32 px cell, inset by the gutter on each side.
        self.assertTrue(ShadowRig.plane_is_atlased(crate))
        gutter = ShadowAtlas.GUTTER
        self.assertAlmostEqual(
            self._record("Crate_shadow")["atlas"]["rect"][0],
            (32 - 2 * gutter) / 32,
            places=6,
        )

    def test_horizon_maps_pack_into_their_own_atlas(self):
        other = cmds.polyCube(name="Crate", width=1, height=1, depth=1)[0]
        cmds.setAttr(f"{other}.translateX", 4.0)
        rigs = ShadowRig.create_per_object(
            [self.cube, other],
            ["shadow_source"],
            texture_res=32,
            rig_type="horizon",
            horizon_bins=8,
            horizon_size=(32, 16),
        )
        self._textures.extend(r.texture_path for r in rigs)
        self._textures.extend(r.horizon_path for r in rigs)
        packed = ShadowRig.pack_atlas([r.shadow_plane for r in rigs])
        self._textures.extend(packed.values())
        self.assertEqual(sorted(packed), ["horizon", "projected"])
        hz = self._record("Box_shadow")["horizon"]
        self.assertEqual(hz["texture"], "shadow_atlas_horizon.png")
        self.assertLess(hz["rect"][0], 0.5)
        # Deleting one rig repacks the survivor alone (a full-width rect).
        ShadowRig.delete_rigs([rigs[0].shadow_plane])
        hz = self._record("Crate_shadow")["horizon"]
        self.assertGreater(hz["rect"][0], 0.9)

    def test_known_producer_registration(self):
        """The shadow producer is wired into FbxUtils._KNOWN_PRODUCERS, so
        run_export_preparers refreshes the channel for any export pipeline."""
        import json

        from mayatk.env_utils.fbx_utils import FbxUtils
        from mayatk.node_utils.data_nodes import DataNodes

        self.assertIn("shadow", FbxUtils._KNOWN_PRODUCERS)
        self._make()
        DataNodes.set_export_string(ShadowRig.SHADOW_METADATA, "")  # stale it
        FbxUtils.run_export_preparers()
        payload = json.loads(DataNodes.get_export_string(ShadowRig.SHADOW_METADATA))
        self.assertEqual(len(payload["planes"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
