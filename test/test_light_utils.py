# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.light_utils — authoring scene lights from fixture geometry."""

import unittest

import maya.cmds as cmds
import mayatk as mtk

from base_test import MayaTkTestCase

def _faces_pointing(shape, axis=1, sign=-1, threshold=0.9):
    """Faces of *shape* whose world normal points along ``sign * axis``.

    Picked by measurement rather than by index: polyCube's face order is a
    convention, and an earlier version of these tests silently tested the TOP
    face while claiming to test the lens.
    """
    picked = []
    for i in range(cmds.polyEvaluate(shape, face=True)):
        face = f"{shape}.f[{i}]"
        normal = mtk.Components.get_normal(face)
        if normal[axis] * sign > threshold:
            picked.append(face)
    return picked


class TestLightsFromGeometry(MayaTkTestCase):
    """Real area lights derived from the geometry that represents a luminaire."""

    def _troffer(self, name, x=0.0, y=390.0, z=0.0, width=372.0, depth=72.0, thick=10.0):
        """A thin ceiling plate, sized like the production module's troffers."""
        cube = cmds.polyCube(
            name=name, width=width, height=thick, depth=depth, constructionHistory=False
        )[0]
        cmds.xform(cube, translation=(x, y, z), worldSpace=True)
        return cube

    def _floor(self):
        floor = cmds.polyPlane(
            name="floor", width=800, height=800, constructionHistory=False
        )[0]
        cmds.xform(floor, translation=(0, 0, 0), worldSpace=True)
        return floor

    def test_one_light_per_shape_sized_to_the_plate(self):
        self._floor()
        plate = self._troffer("troffer")
        created = mtk.LightUtils.lights_from_geometry([plate])

        self.assertEqual(len(created), 1)
        transform = created[0]
        shape = cmds.listRelatives(transform, shapes=True, fullPath=True)[0]
        self.assertEqual(cmds.nodeType(shape), "areaLight")

        # Maya's area light is 2x2 in local space, so scale carries the real size.
        scale = cmds.getAttr(f"{transform}.scale")[0]
        self.assertAlmostEqual(scale[0] * 2.0, 372.0, places=3)
        self.assertAlmostEqual(scale[1] * 2.0, 72.0, places=3)

    def test_created_light_emits_per_area_not_normalized(self):
        # Arnold's normalize spreads the intensity over the emitter, so a
        # fixture-sized plate baked ~100x dimmer than its intensity suggested
        # (measured on a production room, licensed solo bake: far wall 0.009
        # normalized vs 1.03 per-area at the same intensities). Authoring is
        # per-area so the number means the same thing at any fixture size.
        # aiNormalize is an mtoa extension attr; skip when Arnold is absent.
        try:
            cmds.loadPlugin("mtoa", quiet=True)
        except RuntimeError:
            self.skipTest("mtoa unavailable")
        self._floor()
        plate = self._troffer("normTroffer")
        transform = mtk.LightUtils.lights_from_geometry([plate])[0]
        shape = cmds.listRelatives(transform, shapes=True, fullPath=True)[0]
        if not cmds.attributeQuery("aiNormalize", node=shape, exists=True):
            self.skipTest("aiNormalize attr unavailable")
        self.assertEqual(cmds.getAttr(f"{shape}.aiNormalize"), 0)

    def test_upgrade_authored_lights_fixes_only_tagged_lights(self):
        # A saved scene keeps the authored lights' marker attr but not the
        # session that made them: lights authored before per-area emission
        # reopen NORMALIZED and bake ~100x dim, and a manual Normalize fix
        # evaporates with every reopen (measured: the production room re-baked
        # at 0.006 mean for exactly this reason). The upgrade is strictly
        # scoped to the tool's own marker -- a hand-authored light is the
        # artist's and must never be touched.
        try:
            cmds.loadPlugin("mtoa", quiet=True)
        except RuntimeError:
            self.skipTest("mtoa unavailable")

        def _area_light():
            t = cmds.shadingNode("areaLight", asLight=True)
            if cmds.nodeType(t) != "transform":
                t = cmds.listRelatives(t, parent=True, fullPath=True)[0]
            t = cmds.ls(t, long=True)[0]
            s = cmds.listRelatives(t, shapes=True, fullPath=True)[0]
            return t, s

        authored_t, authored_s = _area_light()
        hand_t, hand_s = _area_light()
        if not cmds.attributeQuery("aiNormalize", node=authored_s, exists=True):
            self.skipTest("aiNormalize attr unavailable")
        for s in (authored_s, hand_s):
            cmds.setAttr(f"{s}.aiNormalize", 1)  # the pre-per-area authoring
        cmds.addAttr(
            authored_t,
            longName=mtk.LightUtils.SOURCE_ATTR,
            dataType="string",
        )
        cmds.setAttr(
            f"{authored_t}.{mtk.LightUtils.SOURCE_ATTR}",
            "|someMesh.f[0]",
            type="string",
        )

        upgraded = mtk.LightUtils.upgrade_authored_lights()

        self.assertEqual(upgraded, [authored_t])
        self.assertEqual(cmds.getAttr(f"{authored_s}.aiNormalize"), 0)
        self.assertEqual(cmds.getAttr(f"{hand_s}.aiNormalize"), 1)
        # Idempotent: a second run finds nothing left to change.
        self.assertEqual(mtk.LightUtils.upgrade_authored_lights(), [])

    def test_a_ceiling_plate_is_placed_below_its_own_housing(self):
        """Inside the housing the plate blocks its own light — the measured bug."""
        self._floor()
        plate = self._troffer("troffer", y=390.0, thick=10.0)
        transform = mtk.LightUtils.lights_from_geometry([plate], offset=1.0)[0]

        y = cmds.xform(transform, query=True, translation=True, worldSpace=True)[1]
        bottom = cmds.exactWorldBoundingBox(plate)[1]
        self.assertLess(y, bottom, "light must clear the plate it came from")
        self.assertAlmostEqual(y, 390.0 - 5.0 - 1.0, places=3)

    def test_a_coplanar_ceiling_grid_all_aims_down(self):
        """Their own centre lies in their plane, so the sign there is noise."""
        self._floor()
        plates = [
            self._troffer("t0", x=-200, y=390.0000),
            self._troffer("t1", x=200, y=390.0001),
            self._troffer("t2", x=-200, y=389.9999, z=400),
            self._troffer("t3", x=200, y=390.0001, z=400),
        ]
        created = mtk.LightUtils.lights_from_geometry(plates)
        self.assertEqual(len(created), 4)
        for transform in created:
            matrix = cmds.xform(transform, query=True, matrix=True, worldSpace=True)
            aim_y = -matrix[9]  # a Maya light emits down its local -Z
            self.assertLess(aim_y, -0.9, f"{transform} aims {aim_y:.3f}, not down")

    def test_face_selection_sizes_the_emitter_to_the_lens(self):
        """The whole point of components: housing is not the emitter."""
        self._floor()
        plate = self._troffer("lens_host", width=372.0, depth=72.0)
        shape = cmds.listRelatives(plate, shapes=True, fullPath=True)[0]
        faces = _faces_pointing(shape)  # the downward face: the lens
        lens_bounds = cmds.exactWorldBoundingBox(faces)

        transform = mtk.LightUtils.lights_from_geometry(faces)[0]
        scale = cmds.getAttr(f"{transform}.scale")[0]
        self.assertAlmostEqual(scale[0] * 2.0, lens_bounds[3] - lens_bounds[0], places=3)
        self.assertAlmostEqual(scale[1] * 2.0, lens_bounds[5] - lens_bounds[2], places=3)

    def test_the_same_faces_spelled_two_ways_build_one_light(self):
        """A viewport face selection is transform-rooted; an API one is not.

        Both name the same faces, so both must land on the same emitter —
        otherwise two lights end up stacked on one lens.
        """
        self._floor()
        plate = self._troffer("dual_spelling")
        shape = cmds.listRelatives(plate, shapes=True, fullPath=True)[0]
        index = _faces_pointing(shape)[0].split("[")[1].rstrip("]")
        created = mtk.LightUtils.lights_from_geometry(
            [f"{plate}.f[{index}]", f"{shape}.f[{index}]"]
        )
        self.assertEqual(len(created), 1, f"grouped separately: {created}")

    def test_source_members_are_stored_as_full_paths(self):
        """A leaf name stops being unique the moment a module is duplicated."""
        self._floor()
        plate = self._troffer("pathy")
        shape = cmds.listRelatives(plate, shapes=True, fullPath=True)[0]
        transform = mtk.LightUtils.lights_from_geometry(_faces_pointing(shape))[0]
        stored = cmds.getAttr(f"{transform}.{mtk.LightUtils.SOURCE_ATTR}")
        self.assertTrue(stored.startswith("|"), f"not a full path: {stored}")

    def test_a_light_in_the_selection_is_skipped_not_measured(self):
        """Re-running over a selection that already contains lights must no-op."""
        self._floor()
        plate = self._troffer("rerun")
        first = mtk.LightUtils.lights_from_geometry([plate])
        self.assertEqual(mtk.LightUtils.lights_from_geometry(first), [])

    def test_colour_comes_from_the_material_emission(self):
        """Property-driven: the fixture's own look-dev decides the light's hue."""
        self._floor()
        plate = self._troffer("emissive_plate")
        mat = cmds.shadingNode("standardSurface", asShader=True, name="lensMat")
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="lensSG")
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(plate, edit=True, forceElement=sg)
        cmds.setAttr(f"{mat}.emissionColor", 1.0, 0.5, 0.25, type="double3")
        # standardSurface gates emission behind a separate weight that defaults
        # to 0 -- colour without it is a material that renders black.
        cmds.setAttr(f"{mat}.emission", 1.0)

        transform = mtk.LightUtils.lights_from_geometry([plate], kelvin=6500)[0]
        light = cmds.listRelatives(transform, shapes=True, fullPath=True)[0]
        color = cmds.getAttr(f"{light}.color")[0]
        # Normalized to peak 1, and it must WIN over the kelvin fallback.
        self.assertAlmostEqual(color[0], 1.0, places=3)
        self.assertAlmostEqual(color[1], 0.5, places=3)
        self.assertAlmostEqual(color[2], 0.25, places=3)

    def test_kelvin_is_the_fallback_when_geometry_carries_no_emission(self):
        self._floor()
        plate = self._troffer("plain_plate")
        transform = mtk.LightUtils.lights_from_geometry([plate], kelvin=4000)[0]
        light = cmds.listRelatives(transform, shapes=True, fullPath=True)[0]
        color = cmds.getAttr(f"{light}.color")[0]
        self.assertAlmostEqual(color[0], 1.0, places=2)
        self.assertLess(color[2], color[1], "4000K must be warm: blue below green")

    def test_the_source_link_is_a_property_and_drives_resync(self):
        """The light is an ordinary light; only a property remembers its origin."""
        self._floor()
        plate = self._troffer("moving_plate", y=390.0)
        transform = mtk.LightUtils.lights_from_geometry([plate])[0]
        self.assertTrue(
            cmds.attributeQuery(
                mtk.LightUtils.SOURCE_ATTR, node=transform, exists=True
            )
        )

        cmds.setAttr(f"{transform}.intensity", 0.42) if cmds.objExists(
            f"{transform}.intensity"
        ) else None
        cmds.xform(plate, translation=(0, 300.0, 0), worldSpace=True)
        updated = mtk.LightUtils.sync_lights_from_geometry()
        self.assertIn(transform, updated)

        y = cmds.xform(transform, query=True, translation=True, worldSpace=True)[1]
        self.assertAlmostEqual(y, 300.0 - 5.0 - 1.0, places=3)

    def test_sync_skips_a_light_whose_source_is_gone(self):
        """A deleted source must leave the light exactly where the artist saw it."""
        self._floor()
        plate = self._troffer("doomed_plate")
        transform = mtk.LightUtils.lights_from_geometry([plate])[0]
        before = cmds.xform(transform, query=True, translation=True, worldSpace=True)
        cmds.delete(plate)

        self.assertNotIn(transform, mtk.LightUtils.sync_lights_from_geometry())
        after = cmds.xform(transform, query=True, translation=True, worldSpace=True)
        for axis in range(3):
            self.assertAlmostEqual(before[axis], after[axis], places=4)

    def test_no_selection_creates_nothing(self):
        cmds.select(clear=True)
        self.assertEqual(mtk.LightUtils.lights_from_geometry(), [])


class TestClusteringAndOrientation(MayaTkTestCase):
    """Connected-face clustering, and the oriented solve that replaces the box."""

    def _merged_troffers(self, count=3, spacing=500.0):
        """One MESH holding *count* disconnected plates — the merged-env case."""
        plates = []
        for i in range(count):
            plate = cmds.polyCube(
                name=f"bar{i}", width=372, height=10, depth=72,
                constructionHistory=False,
            )[0]
            cmds.xform(plate, translation=(i * spacing, 390, 0), worldSpace=True)
            plates.append(plate)
        merged = cmds.polyUnite(plates, name="merged_bars", constructionHistory=False)[0]
        cmds.polyPlane(name="floor", width=4000, height=4000, constructionHistory=False)
        return cmds.listRelatives(merged, shapes=True, fullPath=True)[0]

    def _downward_faces(self, shape):
        """The downward face of every plate in the merged mesh."""
        return _faces_pointing(shape)

    def test_connected_faces_cluster_into_one_light_each(self):
        """A single volume over N separate bars is one light spanning the room."""
        shape = self._merged_troffers(count=3)
        faces = self._downward_faces(shape)
        created = mtk.LightUtils.lights_from_geometry(faces, cluster="shell")
        self.assertEqual(len(created), 3, f"expected one per bar, got {created}")
        for transform in created:
            scale = cmds.getAttr(f"{transform}.scale")[0]
            self.assertAlmostEqual(scale[0] * 2.0, 372.0, delta=1.0)

    def test_object_clustering_keeps_the_old_single_light(self):
        shape = self._merged_troffers(count=3)
        created = mtk.LightUtils.lights_from_geometry(
            self._downward_faces(shape), cluster="object"
        )
        self.assertEqual(len(created), 1)
        scale = cmds.getAttr(f"{created[0]}.scale")[0]
        self.assertGreater(scale[0] * 2.0, 1000.0, "should span all three bars")

    def test_face_clustering_gives_every_face_its_own(self):
        shape = self._merged_troffers(count=2)
        created = mtk.LightUtils.lights_from_geometry(
            self._downward_faces(shape), cluster="face"
        )
        self.assertEqual(len(created), 2)

    def test_a_rotated_plate_keeps_its_real_size(self):
        """The oriented solve's whole point: a box inflates a 45-degree plate."""
        cmds.polyPlane(name="floor", width=2000, height=2000, constructionHistory=False)
        plate = cmds.polyCube(
            name="raked", width=372, height=10, depth=72, constructionHistory=False
        )[0]
        cmds.xform(plate, translation=(0, 390, 0), rotation=(0, 45, 0), worldSpace=True)
        shape = cmds.listRelatives(plate, shapes=True, fullPath=True)[0]

        faces = _faces_pointing(shape, threshold=0.5)
        transform = mtk.LightUtils.lights_from_geometry(faces)[0]
        scale = cmds.getAttr(f"{transform}.scale")[0]
        self.assertAlmostEqual(scale[0] * 2.0, 372.0, delta=0.5)
        self.assertAlmostEqual(scale[1] * 2.0, 72.0, delta=0.5)

    def test_a_tilted_plate_aims_along_its_own_normal(self):
        """A face knows which way it faces; no toward-guess involved."""
        import math

        cmds.polyPlane(name="floor", width=2000, height=2000, constructionHistory=False)
        plate = cmds.polyCube(
            name="tilted", width=372, height=10, depth=72, constructionHistory=False
        )[0]
        cmds.xform(plate, translation=(0, 390, 0), rotation=(30, 0, 0), worldSpace=True)
        shape = cmds.listRelatives(plate, shapes=True, fullPath=True)[0]

        faces = _faces_pointing(shape, threshold=0.5)
        expected = mtk.Components.get_normal(faces[0])

        transform = mtk.LightUtils.lights_from_geometry(faces)[0]
        matrix = cmds.xform(transform, query=True, matrix=True, worldSpace=True)
        aim = (-matrix[8], -matrix[9], -matrix[10])
        # The light must follow the FACE, not a guessed "down": a 30-degree
        # tilt leans the normal out of vertical by exactly that much.
        self.assertAlmostEqual(abs(expected[2]), math.sin(math.radians(30)), places=3)
        for got, want in zip(aim, expected):
            self.assertAlmostEqual(got, want, places=3)


class TestMarkerAndTeardown(MayaTkTestCase):
    """The marker attribute as a cross-session handle."""

    def _plate(self, name, x=0.0):
        cmds.polyPlane(name=f"floor_{name}", width=2000, height=2000,
                       constructionHistory=False)
        plate = cmds.polyCube(
            name=name, width=372, height=10, depth=72, constructionHistory=False
        )[0]
        cmds.xform(plate, translation=(x, 390, 0), worldSpace=True)
        return plate

    def test_generated_lights_finds_every_marked_light(self):
        mtk.LightUtils.lights_from_geometry([self._plate("a", 0)])
        mtk.LightUtils.lights_from_geometry([self._plate("b", 900)])
        self.assertEqual(len(mtk.LightUtils.generated_lights()), 2)

    def test_remove_lights_tears_down_everything_it_generated(self):
        mtk.LightUtils.lights_from_geometry([self._plate("a", 0)])
        mtk.LightUtils.lights_from_geometry([self._plate("b", 900)])
        removed = mtk.LightUtils.remove_lights()

        self.assertEqual(len(removed), 2)
        for transform in removed:
            self.assertFalse(cmds.objExists(transform))

    def test_a_namespaced_light_is_still_found_and_removed(self):
        """A plug pattern matches only the current namespace without recursion.

        Referenced and namespaced scenes are routine here, and a teardown that
        silently skips them is worse than one that fails.
        """
        created = mtk.LightUtils.lights_from_geometry([self._plate("nsplate")])
        cmds.namespace(add="lightns")
        cmds.rename(created[0], "lightns:nsLight")

        found = mtk.LightUtils.generated_lights()
        self.assertEqual(len(found), 1, f"namespaced light escaped the query: {found}")
        mtk.LightUtils.remove_lights()
        self.assertEqual(mtk.LightUtils.generated_lights(), [])

    def test_remove_lights_leaves_hand_authored_lights_alone(self):
        """Only what this tool marked is torn down — safe on someone else's scene."""
        hand = cmds.shadingNode("areaLight", asLight=True, name="handAuthored")
        mtk.LightUtils.lights_from_geometry([self._plate("gen")])
        mtk.LightUtils.remove_lights()
        self.assertTrue(cmds.objExists(hand))
        self.assertEqual(mtk.LightUtils.generated_lights(), [])


class TestInstancedFixtures(MayaTkTestCase):
    """Instanced modules — how a ceiling grid or a corridor is actually built.

    Every instance of a shape is its own fixture, and none of the three things
    a light is derived from (which paths exist, where the room's centre is,
    what to call the result) can be answered from the shape node alone.
    """

    def _module(self, name, plate_x=200.0):
        """A floor tile plus one wall plate, thin in X, at the tile's +X edge."""
        tile = cmds.polyPlane(
            name=f"{name}_tile", width=400, height=400, constructionHistory=False
        )[0]
        plate = cmds.polyCube(
            name=f"{name}_plate", width=10, height=100, depth=200,
            constructionHistory=False,
        )[0]
        cmds.xform(plate, translation=(plate_x, 50, 0), worldSpace=True)
        return cmds.group([tile, plate], name=name)

    @staticmethod
    def _aim(transform):
        """World emission direction — a Maya light emits down its local -Z."""
        matrix = cmds.xform(transform, query=True, matrix=True, worldSpace=True)
        return (-matrix[8], -matrix[9], -matrix[10])

    def test_each_instance_gets_its_own_light_named_for_the_instance(self):
        """Instances share one shape, so the SHAPE's name identifies nothing."""
        cmds.polyPlane(name="floor", width=4000, height=4000, constructionHistory=False)
        lens = cmds.polyCube(
            name="lens", width=372, height=10, depth=72, constructionHistory=False
        )[0]
        cmds.xform(lens, translation=(0, 390, 0), worldSpace=True)
        fixtures = [cmds.ls(lens, long=True)[0]]
        for i, x in enumerate((600, 1200), start=1):
            inst = cmds.instance(lens, name=f"lens_inst{i}")[0]
            cmds.xform(inst, translation=(x, 390, 0), worldSpace=True)
            fixtures.append(cmds.ls(inst, long=True)[0])

        created = mtk.LightUtils.lights_from_geometry(fixtures)
        self.assertEqual(len(created), 3, f"one light per instance: {created}")

        # Placed at its OWN instance, not stacked on the original.
        xs = sorted(
            round(cmds.xform(t, query=True, translation=True, worldSpace=True)[0])
            for t in created
        )
        self.assertEqual(xs, [0, 600, 1200])

        # And named after the instance the artist sees in the outliner, not
        # after the one shape node all three of them share.
        for transform in created:
            source = cmds.getAttr(f"{transform}.{mtk.LightUtils.SOURCE_ATTR}")
            instance = source.split(",")[0].split(".")[0].rsplit("|", 2)[-2]
            self.assertTrue(
                transform.rsplit("|", 1)[-1].startswith(instance),
                f"{transform} is not named for its source instance {instance}",
            )

    def test_the_aim_reference_counts_every_instance(self):
        """``ls`` reports one path per shape NODE, so instances vanish from it.

        A corridor of instanced modules then has its centre collapsed onto the
        original, and the wall plates near that end aim OUT of the room.
        """
        modules = [self._module("module")]
        for i in range(1, 4):
            inst = cmds.instance(modules[0], name=f"module_inst{i}")[0]
            cmds.xform(inst, translation=(i * 400, 0, 0), worldSpace=True)
            modules.append(inst)

        plates = []
        for module in cmds.ls(modules, long=True):
            children = (
                cmds.listRelatives(module, children=True, fullPath=True,
                                   type="transform")
                or []
            )
            plates.extend([c for c in children if c.endswith("_plate")])
        self.assertEqual(len(plates), 4)

        # The corridor runs x = -200 .. 1405, so its centre is at x ~ 602: the
        # first module's plate (x = 200) sits BELOW it and must face +X.
        first = min(plates, key=lambda p: cmds.exactWorldBoundingBox(p)[0])
        created = mtk.LightUtils.lights_from_geometry([first])
        aim = self._aim(created[0])
        self.assertGreater(
            aim[0], 0.9, f"the near plate aims {aim}, out of the corridor"
        )

    def test_a_deformed_mesh_in_a_group_still_builds_one_light(self):
        """A deformer leaves an orig shape, and an intermediate mesh IS a mesh.

        The descent's surfaceShape filter cannot tell them apart, so it is
        ``noIntermediate`` that keeps a skinned fixture from getting two lights
        stacked on it.
        """
        cmds.polyPlane(name="floor", width=4000, height=4000, constructionHistory=False)
        lens = cmds.polyCube(name="lens", width=372, height=10, depth=72)[0]
        cmds.select(lens, replace=True)
        cmds.cluster(name="lensCluster")
        module = cmds.group(lens, name="fixture")
        cmds.xform(module, translation=(0, 390, 0), worldSpace=True)
        self.assertEqual(
            len(cmds.listRelatives(lens, shapes=True, fullPath=True)),
            2,
            "the deformer should have left an orig shape to filter",
        )

        created = mtk.LightUtils.lights_from_geometry(cmds.ls(module, long=True))
        self.assertEqual(len(created), 1, f"the orig shape got its own light: {created}")

    def test_geometry_named_twice_is_stored_once(self):
        """Selecting a group AND a mesh inside it names the same shape twice.

        One light either way, but the members ride on the light's source
        property and are re-read on every sync, so a repeat must not be stored.
        """
        cmds.polyPlane(name="floor", width=4000, height=4000, constructionHistory=False)
        lens = cmds.polyCube(
            name="lens", width=372, height=10, depth=72, constructionHistory=False
        )[0]
        module = cmds.group(lens, name="fixture")
        cmds.xform(module, translation=(0, 390, 0), worldSpace=True)

        created = mtk.LightUtils.lights_from_geometry(cmds.ls([module, lens], long=True))
        self.assertEqual(len(created), 1, f"one emitter per shape: {created}")
        stored = cmds.getAttr(f"{created[0]}.{mtk.LightUtils.SOURCE_ATTR}").split(",")
        self.assertEqual(stored, sorted(set(stored)), f"source repeats itself: {stored}")

    def test_selecting_an_instanced_group_builds_its_fixtures(self):
        """A fixture MODULE is what gets instanced — and what gets selected."""
        cmds.polyPlane(name="floor", width=4000, height=4000, constructionHistory=False)
        lens = cmds.polyCube(
            name="lens", width=372, height=10, depth=72, constructionHistory=False
        )[0]
        module = cmds.group([lens], name="fixture")
        cmds.xform(module, translation=(0, 390, 0), worldSpace=True)
        modules = [module]
        for i, x in enumerate((600, 1200), start=1):
            inst = cmds.instance(module, name=f"fixture_inst{i}")[0]
            cmds.xform(inst, translation=(x, 390, 0), worldSpace=True)
            modules.append(inst)

        created = mtk.LightUtils.lights_from_geometry(cmds.ls(modules, long=True))
        self.assertEqual(len(created), 3, f"one light per module: {created}")
        xs = sorted(
            round(cmds.xform(t, query=True, translation=True, worldSpace=True)[0])
            for t in created
        )
        self.assertEqual(xs, [0, 600, 1200])


if __name__ == "__main__":
    unittest.main(verbosity=2)
