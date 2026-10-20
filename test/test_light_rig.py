# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.light_utils.light_rig -- save, manage, swap and restore named light rigs."""

import math
import os
import unittest

import maya.cmds as cmds
import pythontk as ptk

from base_test import MayaTkTestCase


def _arnold_loadable() -> bool:
    """True when mtoa loads, so Arnold light types and attributes are registered."""
    try:
        if not cmds.pluginInfo("mtoa", q=True, loaded=True):
            cmds.loadPlugin("mtoa")
        return bool(cmds.pluginInfo("mtoa", q=True, loaded=True))
    except Exception:
        return False


# A 1x1 PNG, so a gobo's texture is a real file.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c4944415408d763f8ffff3f0005fe02fea7d6a0f90000000049454e44ae426082"
)


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(
        os.path.normpath(b)
    )


class TestLightRigPayload(unittest.TestCase):
    """The scene-free half: spelling a payload canonically and comparing two."""

    @classmethod
    def setUpClass(cls):
        from mayatk.light_utils.light_rig import LightRig

        cls.LightRig = LightRig

    def test_the_neutral_vocabulary_is_pythontks(self):
        """The payload contract is declared once (ptk.LightRigLight): every type
        and setting it names maps onto Maya, and a light's keys are its fields."""
        self.assertEqual(set(self.LightRig.TYPES), set(ptk.LightRigLight.TYPES))
        self.assertEqual(set(self.LightRig.FRIENDLY), set(ptk.LightRigLight.SETTINGS))
        keys = set(self.LightRig._LIGHT_KEYS) | set(self.LightRig.FRIENDLY) | set(self.LightRig.NOTES)
        self.assertEqual(keys, set(ptk.LightRigLight.__dataclass_fields__))

    def test_a_fixture_lights_the_room_one_way_at_a_time(self):
        """A fixture's mode is plain overrides on its meshes and lamps; what
        the rig names outright wins over the mode's."""
        fixture = {"name": "PANELS", "meshes": ["PANEL_MESH"], "lamps": {"PANEL_LAMP": 4.0}}
        seen = lambda mode, **rig: self.LightRig.normalize(  # noqa: E731
            dict({"version": 1, "lights": [], "fixtures": [dict(fixture, mode=mode)]}, **rig)
        )["existing"]
        vis = ("aiVisibleInDiffuseReflection", "aiVisibleInDiffuseTransmission")
        glow, emissive, lamp = seen("glow"), seen("emissive"), seen("lamp")
        self.assertEqual([glow["PANEL_MESH"][a] for a in vis], [False, False])
        self.assertEqual([emissive["PANEL_MESH"][a] for a in vis], [True, True])
        self.assertEqual([lamp["PANEL_MESH"][a] for a in vis], [False, False])
        self.assertEqual(
            [glow["PANEL_LAMP"], emissive["PANEL_LAMP"], lamp["PANEL_LAMP"]],
            [{"intensity": 0.0}, {"intensity": 0.0}, {"intensity": 4.0}],
        )
        named = seen("lamp", existing={"PANEL_LAMP": {"intensity": 2.5}})
        self.assertEqual(named["PANEL_LAMP"], {"intensity": 2.5})
        with self.assertRaises(ValueError):
            seen("lit")
        self.assertEqual(set(self.LightRig.FIXTURE_MODES), set(ptk.LightRigFixture.MODES))

    def test_the_default_library_is_the_one_every_host_shares(self):
        from pythontk.core_utils.user_config import CONFIG_ROOT_ENV_VAR

        temp = ptk.TempArtifacts("mayatk_rig_library_test", policy="scoped")
        self.addCleanup(temp.cleanup, force=True)
        root = str(temp.dir_path())
        prev = os.environ.get(CONFIG_ROOT_ENV_VAR)
        os.environ[CONFIG_ROOT_ENV_VAR] = root
        self.addCleanup(
            lambda: os.environ.pop(CONFIG_ROOT_ENV_VAR, None)
            if prev is None
            else os.environ.__setitem__(CONFIG_ROOT_ENV_VAR, prev)
        )
        self.assertEqual(
            self.LightRig().store.user_dir, ptk.LightRigPayload.library().user_dir
        )

    def test_normalize_spells_a_lookdev_spec_canonically(self):
        rig = self.LightRig.normalize(
            {
                "lights": [
                    {
                        "name": "L",
                        "type": "spot",
                        "pos": [0, 10, 0],
                        "aim": [0, 0, 0],
                        "cone": 30,
                        "exposure": 2,
                        "kelvin_note": 3200,
                        "up": [0, 1, 0],
                    }
                ]
            }
        )
        (light,) = rig["lights"]
        self.assertEqual(rig["version"], 1)
        self.assertEqual(light["type"], "spotLight")
        self.assertEqual(light["attrs"], {"coneAngle": 30, "aiExposure": 2})
        self.assertAlmostEqual(light["rot"][0], -90, places=4)  # aimed straight down
        self.assertNotIn("aim", light)

    def test_an_explicit_rotation_wins_over_an_aim(self):
        rig = self.LightRig.normalize(
            {
                "lights": [
                    {
                        "name": "L",
                        "type": "area",
                        "pos": [0, 0, 0],
                        "aim": [0, -1, 0],
                        "rot": [-90, 0, 0],
                    }
                ]
            }
        )
        self.assertEqual(rig["lights"][0]["rot"], [-90, 0, 0])

    def test_a_kelvin_without_a_colour_uses_arnolds_colour_temperature(self):
        rig = self.LightRig.normalize(
            {
                "lights": [
                    {"name": "L", "type": "point", "pos": [0, 0, 0], "kelvin": 3200}
                ]
            }
        )
        self.assertEqual(
            rig["lights"][0]["attrs"],
            {"aiUseColorTemperature": 1, "aiColorTemperature": 3200},
        )

    def test_a_gobo_becomes_a_filter(self):
        rig = self.LightRig.normalize(
            {
                "lights": [
                    {
                        "name": "L",
                        "type": "spot",
                        "pos": [0, 0, 0],
                        "gobo": "C:/t/g.png",
                        "gobo_rotate": 30,
                    }
                ]
            }
        )
        (filt,) = rig["lights"][0]["filters"]
        self.assertEqual(filt["type"], "aiGobo")
        self.assertEqual(
            filt["attrs"], {"rotate[0]": 30}
        )  # mtoa declares the gobo's rotate a multi
        self.assertEqual(filt["textures"]["slidemap"]["file"], "C:/t/g.png")

    def test_unknown_light_keys_are_refused_by_name(self):
        with self.assertRaises(ValueError) as ctx:
            self.LightRig.normalize(
                {
                    "lights": [
                        {"name": "L", "type": "spot", "pos": [0, 0, 0], "konus": 30}
                    ]
                }
            )
        self.assertIn("konus", str(ctx.exception))

    def test_a_newer_payload_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            self.LightRig.normalize({"version": 99, "lights": []})
        self.assertIn("99", str(ctx.exception))

    def test_diff_names_what_changed(self):
        a = {
            "lights": [
                {
                    "name": "L1",
                    "type": "spotLight",
                    "pos": [0, 0, 0],
                    "attrs": {"intensity": 1.0},
                }
            ]
        }
        b = {
            "lights": [
                {
                    "name": "L1",
                    "type": "spotLight",
                    "pos": [0, 0, 0],
                    "attrs": {"intensity": 2.0},
                },
                {"name": "L2", "type": "pointLight", "pos": [0, 0, 0]},
            ]
        }
        changes = self.LightRig.diff(a, b)
        self.assertTrue(any("L1" in c and "intensity" in c for c in changes), changes)
        self.assertTrue(any(c.startswith("+ L2") for c in changes), changes)
        self.assertEqual(self.LightRig.diff(a, a), [])

    def test_diff_reads_two_eulers_of_one_orientation_as_the_same(self):
        # Maya queries an aimed light back as another solution of the same rotation (production MOODY rig)
        a = {
            "lights": [
                {
                    "name": "L",
                    "type": "spotLight",
                    "pos": [0, 0, 0],
                    "rot": [135.1, 37.41, -180],
                }
            ]
        }
        b = {
            "lights": [
                {
                    "name": "L",
                    "type": "spotLight",
                    "pos": [0, 0, 0],
                    "rot": [-44.9, 142.59, 0],
                }
            ]
        }
        self.assertEqual(self.LightRig.diff(a, b), [])
        c = {
            "lights": [
                {
                    "name": "L",
                    "type": "spotLight",
                    "pos": [0, 0, 0],
                    "rot": [-44.9, 142.59, 10],
                }
            ]
        }
        self.assertEqual(len(self.LightRig.diff(a, c)), 1)

    def test_diff_ignores_float_noise(self):
        a = {
            "lights": [
                {
                    "name": "L",
                    "type": "pointLight",
                    "pos": [1.0, 2.0, 3.0],
                    "attrs": {"intensity": 1.0},
                }
            ]
        }
        b = {
            "lights": [
                {
                    "name": "L",
                    "type": "pointLight",
                    "pos": [1.0000001, 2.0, 3.0],
                    "attrs": {"intensity": 1.0000001},
                }
            ]
        }
        self.assertEqual(self.LightRig.diff(a, b), [])


@unittest.skipUnless(_arnold_loadable(), "Arnold (mtoa) plugin not available")
class TestLightRig(MayaTkTestCase):
    """The scene half: capture, apply, swap back, the library."""

    def setUp(self):
        super().setUp()
        from mayatk.light_utils.light_rig import LightRig

        self.LightRig = LightRig
        self.temp = ptk.TempArtifacts("mayatk_light_rig_test", policy="scoped")
        self.dir = str(self.temp.dir_path())
        self.store = ptk.PresetStore(
            "light_rigs", user_dir=os.path.join(self.dir, "rigs")
        )
        self.rig = LightRig(group="TEST_RIG", store=self.store)

    def tearDown(self):
        self.temp.cleanup(force=True)
        super().tearDown()

    # ------------------------------------------------------------------ helpers
    def _light(self, kind, name, pos, rot=(0, 0, 0), scale=(1, 1, 1), **attrs):
        tf = cmds.shadingNode(kind, asLight=True)
        tf = cmds.rename(tf, name)
        shape = cmds.listRelatives(tf, shapes=True, fullPath=True)[0]
        cmds.xform(tf, ws=True, t=pos, ro=rot)
        cmds.xform(tf, s=scale)
        for attr, value in attrs.items():
            if isinstance(value, (list, tuple)):
                cmds.setAttr(f"{shape}.{attr}", *value, type="double3")
            else:
                cmds.setAttr(f"{shape}.{attr}", value)
        return tf

    def _build_rig(self, group="TEST_RIG"):
        lights = [
            self._light(
                "spotLight",
                "L_SPOT",
                (10, 200, 30),
                (-30, 45, 0),
                intensity=4.5,
                color=(1, 0.5, 0.25),
                coneAngle=33,
                penumbraAngle=5,
                dropoff=2,
                aiExposure=3,
            ),
            self._light(
                "pointLight", "L_POINT", (-50, 150, 0), intensity=2, aiRadius=2.0
            ),
            self._light(
                "areaLight",
                "L_AREA",
                (0, 300, 0),
                (-90, 0, 0),
                (60, 30, 1),
                aiSpecular=0,
                aiNormalize=0,
            ),
            self._light(
                "aiAreaLight", "L_AIAREA", (80, 100, -40), aiSpread=0.4, aiExposure=5
            ),
        ]
        return cmds.group(lights, name=group, world=True)

    def _gobo_rig(self):
        png = os.path.join(self.dir, "gobo.png")
        with open(png, "wb") as f:
            f.write(_PNG)
        rig = {
            "version": 1,
            "group": "TEST_RIG",
            "lights": [
                {
                    "name": "L_GOBO",
                    "type": "spot",
                    "pos": [0, 100, 0],
                    "aim": [0, 0, 0],
                    "cone": 50,
                    "gobo": png,
                    "gobo_rotate": 30,
                }
            ],
        }
        return rig, png

    def _shape(self, light):
        return cmds.listRelatives(f"TEST_RIG|{light}", shapes=True, fullPath=True)[0]

    def _units(self, linear, angle):
        """Switch the scene's working units; the test's end puts back the ones it found."""
        found = (
            cmds.currentUnit(query=True, linear=True),
            cmds.currentUnit(query=True, angle=True),
        )
        self.addCleanup(cmds.currentUnit, linear=found[0], angle=found[1])
        cmds.currentUnit(linear=linear, angle=angle)

    def _placement(self):
        """``{light: (world matrix, cone degrees, aiRadius)}`` for the rig's
        lights, read in Maya's internal units: the same numbers whatever units
        the scene works in."""
        from mayatk.core_utils.units import Units

        out = {}
        for tf in cmds.listRelatives("TEST_RIG", children=True, fullPath=True) or []:
            shape = cmds.listRelatives(tf, shapes=True, fullPath=True)[0]
            cone = radius = None
            if cmds.attributeQuery("coneAngle", node=shape, exists=True):
                cone = Units.angle(cmds.getAttr(shape + ".coneAngle"), "working", "deg")
            if cmds.attributeQuery("aiRadius", node=shape, exists=True):
                radius = cmds.getAttr(shape + ".aiRadius")
            matrix = cmds.xform(tf, query=True, worldSpace=True, matrix=True)
            out[tf.split("|")[-1]] = (matrix, cone, radius)
        return out

    def _assert_same_placement(self, got, want):
        self.assertEqual(sorted(got), sorted(want))
        for name, (matrix, cone, radius) in want.items():
            got_matrix, got_cone, got_radius = got[name]
            for g, w in zip(got_matrix, matrix):
                self.assertAlmostEqual(
                    g, w, places=3, msg=f"{name}: matrix {got_matrix} != {matrix}"
                )
            for g, w, what in ((got_cone, cone, "cone"), (got_radius, radius, "radius")):
                if w is None:
                    self.assertIsNone(g, f"{name}: {what}")
                else:
                    self.assertAlmostEqual(g, w, places=4, msg=f"{name}: {what}")

    # ------------------------------------------------------------------ capture / apply
    def test_capture_round_trips_every_light_type(self):
        self._build_rig()
        before = self.rig.capture()
        self.assertEqual(
            sorted(light["name"] for light in before["lights"]),
            ["L_AIAREA", "L_AREA", "L_POINT", "L_SPOT"],
        )
        self.rig.remove()
        self.assertFalse(cmds.objExists("TEST_RIG"))
        self.rig.apply(before, snapshot=False)
        self.assertEqual(self.LightRig.diff(before, self.rig.capture()), [])

    def test_capture_keeps_only_what_differs_from_a_fresh_light(self):
        cmds.group(
            self._light("spotLight", "L_PLAIN", (0, 0, 0)), name="TEST_RIG", world=True
        )
        (light,) = self.rig.capture()["lights"]
        self.assertEqual(light["type"], "spotLight")
        self.assertEqual(light.get("attrs", {}), {})

    def test_capture_records_the_values_set(self):
        self._build_rig()
        spot = next(
            light for light in self.rig.capture()["lights"] if light["name"] == "L_SPOT"
        )
        self.assertAlmostEqual(spot["attrs"]["coneAngle"], 33, places=4)
        self.assertAlmostEqual(spot["attrs"]["aiExposure"], 3, places=4)
        for got, want in zip(spot["attrs"]["color"], (1, 0.5, 0.25)):
            self.assertAlmostEqual(got, want, places=4)
        for got, want in zip(spot["pos"], (10, 200, 30)):
            self.assertAlmostEqual(got, want, places=3)

    def test_a_lookdev_spec_builds_the_light_it_describes(self):
        self.rig.apply(
            {
                "version": 1,
                "group": "TEST_RIG",
                "lights": [
                    {
                        "name": "L_KEY",
                        "type": "spot",
                        "pos": [0, 100, 100],
                        "aim": [0, 0, 0],
                        "cone": 40,
                        "penumbra": 4,
                        "exposure": 12,
                        "intensity": 2,
                        "color": [1, 0.8, 0.6],
                        "radius": 1,
                        "kelvin_note": 3200,
                    }
                ],
            },
            snapshot=False,
        )
        shape = self._shape("L_KEY")
        self.assertEqual(cmds.nodeType(shape), "spotLight")
        self.assertAlmostEqual(cmds.getAttr(shape + ".coneAngle"), 40, places=4)
        self.assertAlmostEqual(cmds.getAttr(shape + ".aiExposure"), 12, places=4)
        m = cmds.xform("TEST_RIG|L_KEY", q=True, ws=True, matrix=True)
        forward = (-m[8], -m[9], -m[10])  # a Maya light shines down its -Z
        for got, want in zip(forward, (0, -1 / math.sqrt(2), -1 / math.sqrt(2))):
            self.assertAlmostEqual(got, want, places=3)

    def test_a_gobo_survives_capture_and_apply(self):
        rig, png = self._gobo_rig()
        self.rig.apply(rig, snapshot=False)
        shape = self._shape("L_GOBO")
        (gobo,) = cmds.listConnections(
            shape + ".aiFilters", source=True, destination=False
        )
        self.assertEqual(cmds.nodeType(gobo), "aiGobo")
        self.assertAlmostEqual(cmds.getAttr(gobo + ".rotate[0]"), 30, places=4)
        (tex,) = cmds.listConnections(
            gobo + ".slidemap", source=True, destination=False
        )
        self.assertTrue(_same_path(cmds.getAttr(tex + ".fileTextureName"), png))
        captured = self.rig.capture()
        (filt,) = captured["lights"][0]["filters"]
        self.assertEqual(filt["type"], "aiGobo")
        self.assertTrue(_same_path(filt["textures"]["slidemap"]["file"], png))
        self.rig.remove()
        self.rig.apply(captured, snapshot=False)
        self.assertEqual(self.LightRig.diff(captured, self.rig.capture()), [])

    def test_replacing_a_rig_leaves_no_orphaned_filters(self):
        rig, png = self._gobo_rig()
        self.rig.apply(rig, snapshot=False)
        self.assertEqual(len(cmds.ls(type="aiGobo")), 1)
        self.rig.apply(
            {"version": 1, "group": "TEST_RIG", "lights": []}, snapshot=False
        )
        self.assertEqual(cmds.ls(type="aiGobo"), [])
        self.assertFalse(
            [
                f
                for f in cmds.ls(type="file")
                if _same_path(cmds.getAttr(f + ".fileTextureName") or "x", png)
            ]
        )

    def test_an_override_wins_over_a_connection_and_swap_back_reconnects_it(self):
        """Portable: a scene may drive what a rig sets (an office's emission
        from its texture's alpha, where the rig was baked with it off). The
        override breaks the connection -- reported -- and swap back and undo
        restore it."""
        mat = cmds.shadingNode("blinn", asShader=True, name="TEST_MAT")
        driver = cmds.shadingNode("multiplyDivide", asUtility=True)
        cmds.connectAttr(driver + ".outputX", mat + ".eccentricity")
        cmds.connectAttr(driver + ".output", mat + ".color")  # a whole compound
        cmds.setAttr(mat + ".reflectivity", lock=True)
        rig = {
            "version": 1,
            "group": "TEST_RIG",
            "lights": [],
            "materials": {
                "TEST_MAT": {
                    "eccentricity": 0.9,
                    "specularRollOff": 0.2,
                    "reflectivity": 0.1,
                    "color": [0.25, 0.5, 0.75],
                }
            },
        }
        report = self.rig.apply(rig)
        self.assertFalse(
            cmds.listConnections(mat + ".eccentricity", source=True, destination=False)
        )
        self.assertAlmostEqual(cmds.getAttr(mat + ".eccentricity"), 0.9, places=5)
        self.assertAlmostEqual(cmds.getAttr(mat + ".specularRollOff"), 0.2, places=5)
        self.assertFalse(cmds.listConnections(mat + ".color", source=True, destination=False))
        self.assertEqual([round(v, 4) for v in cmds.getAttr(mat + ".color")[0]], [0.25, 0.5, 0.75])
        self.assertTrue(
            any(driver in line and "TEST_MAT.eccentricity" in line for line in report["disconnected"]),
            report,
        )
        self.assertTrue(  # a locked plug is still the scene's
            any("TEST_MAT.reflectivity" in line for line in report["skipped"]), report
        )
        self.rig.swap_back()
        self.assertEqual(
            cmds.listConnections(mat + ".eccentricity", source=True, destination=False),
            [driver],
        )
        self.assertEqual(
            cmds.listConnections(mat + ".color", source=True, destination=False, plugs=True),
            [driver + ".output"],
        )
        self.rig.apply(rig)  # and undo puts it back too
        cmds.undo()
        self.assertEqual(
            cmds.listConnections(mat + ".eccentricity", source=True, destination=False),
            [driver],
        )

    def _bridged_panel(self):
        """A standardSurface panel glowing from its emissive map, and its Arnold bridge."""
        if not _arnold_loadable():
            self.skipTest("mtoa unavailable")
        from mayatk.mat_utils.arnold_bridge import ArnoldBridge

        path = os.path.join(self.dir, "panel_Emissive.png")
        with open(path, "wb") as f:
            f.write(_PNG)
        mat = cmds.shadingNode("standardSurface", asShader=True, name="PANEL_MAT")
        tex = cmds.shadingNode("file", asTexture=True, name="panel_emissive_file")
        cmds.setAttr(tex + ".fileTextureName", path, type="string")
        cmds.connectAttr(tex + ".outColor", mat + ".emissionColor")
        cmds.setAttr(mat + ".emission", 0.5)
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="PANEL_SG")
        cmds.connectAttr(mat + ".outColor", sg + ".surfaceShader")
        cmds.sets(cmds.polyCube(name="panel_geo")[0], edit=True, forceElement=sg)
        (bridge,) = ArnoldBridge().add(materials=mat)
        return mat, bridge

    def test_an_override_on_a_bridges_emission_lands_on_its_material(self):
        """The export ships the material and the bridge renders it through the
        link: set on the bridge, a rig set the bake apart from the deliverable
        (2026-10-18: a MOODY room baked its panels lit and shipped them dark)."""
        mat, bridge = self._bridged_panel()
        rig = {"version": 1, "group": "TEST_RIG", "lights": [], "materials": {bridge: {"emission": 2.0}}}
        report = self.rig.apply(rig)
        self.assertAlmostEqual(cmds.getAttr(mat + ".emission"), 2.0, places=5)
        self.assertTrue(  # the bridge still follows its map, and its map its material
            cmds.listConnections(bridge + ".emission", source=True, destination=False)
        )
        self.assertFalse(report["disconnected"], report)
        self.assertEqual(self.rig.compare(rig), [])
        self.rig.swap_back()
        self.assertAlmostEqual(cmds.getAttr(mat + ".emission"), 0.5, places=5)

    def test_a_bridge_override_yields_to_its_materials_own(self):
        mat, bridge = self._bridged_panel()
        rig = {
            "version": 1,
            "group": "TEST_RIG",
            "lights": [],
            "materials": {mat: {"emission": 0.75}, bridge: {"emission": 0.0}},
        }
        report = self.rig.apply(rig)
        self.assertAlmostEqual(cmds.getAttr(mat + ".emission"), 0.75, places=5)
        self.assertTrue(
            any(line.startswith(bridge) and "0.75" in line for line in report["skipped"]),
            report,
        )
        self.assertEqual(self.rig.compare(rig), [])  # no phantom difference after

    def test_a_compound_with_a_driven_child_is_left_alone_and_the_apply_goes_on(self):
        """A colour whose red channel alone is driven: setting the colour would
        fail, so the override is reported and the rest of the rig applies."""
        mat = cmds.shadingNode("blinn", asShader=True, name="TEST_MAT")
        driver = cmds.shadingNode("multiplyDivide", asUtility=True)
        cmds.connectAttr(driver + ".outputX", mat + ".colorR")
        report = self.rig.apply(
            {
                "version": 1,
                "group": "TEST_RIG",
                "lights": [],
                "materials": {"TEST_MAT": {"color": [0.5, 0.5, 0.5], "eccentricity": 0.7}},
            }
        )
        self.assertTrue(any("TEST_MAT.color" in line for line in report["skipped"]), report)
        self.assertEqual(
            cmds.listConnections(mat + ".colorR", source=True, destination=False), [driver]
        )
        self.assertAlmostEqual(cmds.getAttr(mat + ".eccentricity"), 0.7, places=5)

    def test_a_rig_stays_where_its_group_lives(self):
        # a scene keeps its rig under a group the exporter skips (TEMP): a swap must not move it out
        cmds.group(empty=True, name="TEMP", world=True)
        self._build_rig()
        cmds.parent("TEST_RIG", "TEMP")
        self.rig.apply(
            {
                "version": 1,
                "group": "TEST_RIG",
                "lights": [{"name": "L_B", "type": "point", "pos": [0, 5, 0]}],
            },
            snapshot=False,
        )
        self.assertEqual(cmds.ls("TEST_RIG", long=True), ["|TEMP|TEST_RIG"])
        self.assertAlmostEqual(
            cmds.xform("|TEMP|TEST_RIG|L_B", q=True, ws=True, t=True)[1], 5, places=4
        )

    def test_one_undo_reverts_a_swap(self):
        self._build_rig()
        before = self.rig.capture()
        cmds.undoInfo(state=True, infinity=True)
        cmds.flushUndo()
        self.rig.apply(
            {
                "version": 1,
                "group": "TEST_RIG",
                "lights": [{"name": "L_B", "type": "point", "pos": [0, 0, 0]}],
            },
            snapshot=False,
        )
        cmds.undo()
        self.assertEqual(self.LightRig.diff(before, self.rig.capture()), [])

    def test_compare_finds_nothing_in_the_rig_just_applied(self):
        office = self._light("pointLight", "OFFICE_LIGHT", (0, 250, 0), intensity=5)
        rig = {
            "version": 1,
            "group": "TEST_RIG",
            "existing": {"OFFICE_LIGHT": {"intensity": 0}},
            "lights": [
                {
                    "name": "L_KEY",
                    "type": "spot",
                    "pos": [0, 100, 100],
                    "aim": [0, 0, 0],
                    "cone": 40,
                    "penumbra": 0,
                    "intensity": 1,
                    "color": [1, 1, 1],
                    "exposure": 12,
                }
            ],
        }  # penumbra, intensity, colour: Maya's defaults
        self.rig.apply(rig, snapshot=False)
        self.assertEqual(self.rig.compare(rig), [])
        cmds.setAttr(self._shape("L_KEY") + ".coneAngle", 25)
        cmds.setAttr(
            cmds.listRelatives(office, shapes=True, fullPath=True)[0] + ".intensity", 2
        )
        changes = self.rig.compare(rig)
        self.assertTrue(
            any("L_KEY" in c and "coneAngle" in c for c in changes), changes
        )
        self.assertTrue(any("OFFICE_LIGHT.intensity" in c for c in changes), changes)
        self.assertEqual(len(changes), 2, changes)

    # ------------------------------------------------------------------ units
    def test_a_rig_saved_in_centimetres_lands_in_place_in_a_metre_scene(self):
        """A payload is centimetres and degrees whatever the scene works in
        (``ptk.LightRigPayload``): every light lands where it was saved, at
        its cone and radius, and the scene keeps its own units (2026-10-19:
        positions rode the working unit, 100x off in a metre scene, while
        ``aiRadius`` -- a plain float, always cm -- stayed right)."""
        self._build_rig()
        placed = self._placement()
        self.rig.save("A")
        self.rig.remove()
        self._units("m", "rad")
        self.rig.apply("A")
        self._assert_same_placement(self._placement(), placed)
        self.assertEqual(self.LightRig.diff(self.rig.load("A"), self.rig.capture()), [])
        self.assertEqual(self.rig.compare("A"), [])
        self.assertEqual(self.rig.applied(), ("A", False))
        self.assertEqual(
            (
                cmds.currentUnit(query=True, linear=True),
                cmds.currentUnit(query=True, angle=True),
            ),
            ("m", "rad"),
        )

    def test_a_rig_saved_in_a_metre_scene_is_centimetres_and_degrees(self):
        self._build_rig()  # L_SPOT at (10, 200, 30) cm, rotated (-30, 45, 0), cone 33
        placed = self._placement()
        self._units("m", "rad")
        self.rig.save("A")
        spot = next(
            light for light in self.rig.load("A")["lights"] if light["name"] == "L_SPOT"
        )
        for got, want in zip(spot["pos"], (10, 200, 30)):
            self.assertAlmostEqual(got, want, places=3)
        self.assertTrue(
            self.LightRig._same_rotation(spot["rot"], 0, [-30, 45, 0], 0, 1e-5),
            spot["rot"],
        )
        self.assertAlmostEqual(spot["attrs"]["coneAngle"], 33, places=3)
        self.rig.remove()
        self._units("cm", "deg")
        self.rig.apply("A")
        self._assert_same_placement(self._placement(), placed)

    def test_swap_back_reconnects_a_driver_as_the_scene_made_it(self):
        """An override is centimetres, but a driver it took over is
        reconnected in the scene's own units: Maya sizes the unit conversion
        it inserts from the working unit at connect time, so a link made in a
        metre scene and put back under centimetres would come back 100x off."""
        self._units("m", "deg")
        lamp = self._light("pointLight", "OFFICE_LIGHT", (0, 0, 0))
        driver = cmds.shadingNode("multiplyDivide", asUtility=True)
        cmds.setAttr(driver + ".input1X", 2.5)  # metres, through a unit conversion
        cmds.connectAttr(driver + ".outputX", lamp + ".translateY")

        def height():  # internal centimetres
            return cmds.xform(lamp, query=True, worldSpace=True, matrix=True)[13]

        self.assertAlmostEqual(height(), 250.0, places=3)
        report = self.rig.apply(
            {
                "version": 1,
                "group": "TEST_RIG",
                "lights": [],
                "existing": {"OFFICE_LIGHT": {"translateY": 120}},
            }
        )
        self.assertTrue(report["disconnected"], report)
        self.assertAlmostEqual(height(), 120.0, places=3)  # the rig's centimetres
        self.rig.swap_back()
        self.assertEqual(
            cmds.listConnections(
                lamp + ".translateY",
                source=True,
                destination=False,
                skipConversionNodes=True,
            ),
            [driver],
        )
        self.assertAlmostEqual(height(), 250.0, places=3)

    # ------------------------------------------------------------------ swap / restore
    def test_apply_replaces_the_rig_and_swap_back_returns_to_it(self):
        self._build_rig()
        self.rig.save("A")
        a = self.rig.capture()
        self.rig.apply(
            {
                "version": 1,
                "group": "TEST_RIG",
                "lights": [
                    {"name": "L_B", "type": "point", "pos": [0, 50, 0], "intensity": 7}
                ],
            }
        )
        self.assertEqual(
            [light["name"] for light in self.rig.capture()["lights"]], ["L_B"]
        )
        self.assertFalse(cmds.objExists("L_SPOT"))
        self.rig.swap_back()
        self.assertEqual(self.LightRig.diff(a, self.rig.capture()), [])
        self.assertEqual(self.rig.applied(), ("A", False))
        self.rig.swap_back()  # a toggle: back to B
        self.assertEqual(
            [light["name"] for light in self.rig.capture()["lights"]], ["L_B"]
        )

    def test_swap_back_restores_what_a_rig_overrode(self):
        office = self._light("pointLight", "OFFICE_LIGHT", (0, 250, 0), intensity=5)
        shape = cmds.listRelatives(office, shapes=True, fullPath=True)[0]
        self.rig.apply(
            {
                "version": 1,
                "group": "TEST_RIG",
                "lights": [],
                "existing": {"OFFICE_LIGHT": {"intensity": 0}},
            }
        )
        self.assertEqual(cmds.getAttr(shape + ".intensity"), 0)
        self.rig.swap_back()
        self.assertAlmostEqual(cmds.getAttr(shape + ".intensity"), 5, places=5)
        self.assertTrue(
            cmds.objExists("OFFICE_LIGHT")
        )  # a rig never deletes a light it does not own

    def test_a_fixtures_mode_lands_and_swap_back_returns_the_last(self):
        mesh = cmds.polyPlane(name="PANEL_MESH")[0]
        mesh_shape = cmds.listRelatives(mesh, shapes=True, fullPath=True)[0]
        lamp = self._light("areaLight", "PANEL_LAMP", (0, 280, 0), intensity=1)
        lamp_shape = cmds.listRelatives(lamp, shapes=True, fullPath=True)[0]

        def rig(mode):
            return {
                "version": 1,
                "group": "TEST_RIG",
                "lights": [],
                "fixtures": [
                    {"name": "PANELS", "mode": mode, "meshes": [mesh], "lamps": {lamp: 6.0}}
                ],
            }

        self.rig.apply(rig("lamp"))
        self.assertFalse(cmds.getAttr(mesh_shape + ".aiVisibleInDiffuseReflection"))
        self.assertAlmostEqual(cmds.getAttr(lamp_shape + ".intensity"), 6.0, places=5)
        self.rig.apply(rig("emissive"))
        self.assertTrue(cmds.getAttr(mesh_shape + ".aiVisibleInDiffuseReflection"))
        self.assertTrue(cmds.getAttr(mesh_shape + ".aiVisibleInDiffuseTransmission"))
        self.assertEqual(cmds.getAttr(lamp_shape + ".intensity"), 0)
        self.assertEqual(self.rig.compare(rig("emissive")), [])  # nothing left to change
        self.rig.swap_back()  # to the lamp mode
        self.assertFalse(cmds.getAttr(mesh_shape + ".aiVisibleInDiffuseReflection"))
        self.assertAlmostEqual(cmds.getAttr(lamp_shape + ".intensity"), 6.0, places=5)

    def test_swap_back_with_nothing_to_return_to_says_so(self):
        with self.assertRaises(LookupError):
            self.rig.swap_back()

    def test_applied_names_the_rig_and_notices_an_edit(self):
        self._build_rig()
        self.rig.save("A")
        self.rig.remove()
        self.assertEqual(self.rig.applied(), (None, False))
        self.rig.apply("A")
        self.assertEqual(self.rig.applied(), ("A", False))
        cmds.setAttr(self._shape("L_POINT") + ".intensity", 9)
        self.assertEqual(self.rig.applied(), ("A", True))

    # ------------------------------------------------------------------ library
    def test_the_library_saves_lists_renames_locks_and_deletes(self):
        self._build_rig()
        self.rig.save("moody", description="first cut")
        self.assertEqual(self.rig.names(), ["moody"])
        self.assertEqual(self.store.info("moody").get("description"), "first cut")
        self.assertEqual(
            self.LightRig.diff(self.rig.load("moody"), self.rig.capture()), []
        )
        self.assertTrue(self.rig.rename("moody", "moody_v2"))
        self.assertEqual(self.rig.names(), ["moody_v2"])
        self.store.set_info("moody_v2", read_only=True)
        with self.assertRaises(PermissionError):
            self.rig.save("moody_v2")
        self.store.set_info("moody_v2", read_only=None)
        self.assertTrue(self.rig.delete("moody_v2"))
        self.assertEqual(self.rig.names(), [])

    def test_saving_over_the_applied_rig_records_its_lineage(self):
        self._build_rig()
        self.rig.save("A")
        cmds.setAttr(self._shape("L_POINT") + ".intensity", 9)
        self.rig.save("B")
        self.assertEqual(self.store.info("B").get("parent"), "A")
        self.assertEqual(self.rig.applied(), ("B", False))

    def test_a_rig_group_carries_no_rig_state(self):
        # a node attribute rides every FBX as a user property: the state lives in the scene record
        self._build_rig()
        self.rig.save("A")
        self.rig.apply(
            {
                "version": 1,
                "group": "TEST_RIG",
                "lights": [{"name": "L_B", "type": "point", "pos": [0, 0, 0]}],
            }
        )
        self.assertIsNone(cmds.listAttr("TEST_RIG", userDefined=True))
        self.assertEqual(self.rig.applied(), (None, False))
        self.rig.swap_back()
        self.assertIsNone(cmds.listAttr("TEST_RIG", userDefined=True))
        self.assertEqual(self.rig.applied(), ("A", False))

    def test_a_swap_adds_nothing_to_the_library(self):
        self._build_rig()
        self.rig.save("A")
        self.rig.apply({"version": 1, "group": "TEST_RIG", "lights": []})
        self.rig.swap_back()
        self.assertEqual(self.store.list(), ["A"])

    def test_swap_back_belongs_to_its_scene(self):
        self._build_rig()
        self.rig.apply({"version": 1, "group": "TEST_RIG", "lights": []})
        cmds.file(new=True, force=True)
        with self.assertRaises(LookupError):
            self.rig.swap_back()

    def test_one_undo_restores_the_rig_the_scene_records(self):
        self._build_rig()
        self.rig.save("A")
        cmds.undoInfo(state=True, infinity=True)
        cmds.flushUndo()
        self.rig.apply(
            {
                "version": 1,
                "group": "TEST_RIG",
                "lights": [{"name": "L_B", "type": "point", "pos": [0, 0, 0]}],
            }
        )
        cmds.undo()
        self.assertEqual(self.rig.applied(), ("A", False))

    def test_a_saved_rig_keeps_the_overrides_and_viewer_settings_it_was_applied_with(
        self,
    ):
        office = self._light("pointLight", "OFFICE_LIGHT", (0, 250, 0), intensity=5)
        self.rig.apply(
            {
                "version": 1,
                "group": "TEST_RIG",
                "lights": [{"name": "L_B", "type": "point", "pos": [0, 0, 0]}],
                "existing": {"OFFICE_LIGHT": {"intensity": 0}},
                "viewer": {"panel_glow": 0.3},
            },
            snapshot=False,
        )
        cmds.setAttr(
            cmds.listRelatives(office, shapes=True, fullPath=True)[0] + ".intensity",
            0.5,
        )  # a hand edit is kept
        self.rig.save("C")
        saved = self.rig.load("C")
        self.assertAlmostEqual(
            saved["existing"]["OFFICE_LIGHT"]["intensity"], 0.5, places=5
        )
        self.assertEqual(saved["viewer"], {"panel_glow": 0.3})


if __name__ == "__main__":
    unittest.main(exit=False)
